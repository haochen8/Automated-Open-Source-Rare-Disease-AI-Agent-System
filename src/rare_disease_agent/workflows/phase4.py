"""Restartable offline synthetic Track 1 dry run and local research report."""

from __future__ import annotations

import hashlib
import secrets
from pathlib import Path

import psutil

from rare_disease_agent.config import load_settings
from rare_disease_agent.llm.mock import MockLLMBackend
from rare_disease_agent.reporting.research import write_report
from rare_disease_agent.resource_management import atomic_json
from rare_disease_agent.storage.parquet import write_variants_parquet
from rare_disease_agent.synthetic.phase4 import EDGE_CASES, generate_case
from rare_disease_agent.tools.variants.preflight import GenomicInput, validate_parquet
from rare_disease_agent.tools.variants.vcf import parse_vcf
from rare_disease_agent.workflows.mock_strategy import phase3_mock_decisions
from rare_disease_agent.workflows.recovery import RestartableRun
from rare_disease_agent.workflows.track1_evidence import Track1EvidenceWorkflow


def _phase4_run(
    directory: Path,
    *,
    case_name: str,
    run_id: str,
    weights: dict | None = None,
    seed: int = 17,
    interrupt_after: str | None = None,
) -> Path:
    settings = load_settings()
    if weights:
        settings.track1.preliminary_scoring = type(settings.track1.preliminary_scoring)(**weights)
    from importlib.resources import files

    resource = files("rare_disease_agent.resources.phenotype")
    hashes = {
        name: hashlib.sha256(resource.joinpath(name).read_bytes()).hexdigest()
        for name in (
            "synthetic_hp.obo",
            "synthetic_genes_to_phenotype.tsv",
            "synthetic_manifest.json",
        )
    }
    package_root = Path(__file__).resolve().parents[1]
    code_digest = hashlib.sha256()
    for source in sorted(package_root.rglob("*.py")):
        code_digest.update(str(source.relative_to(package_root)).encode())
        code_digest.update(source.read_bytes())
    runner = RestartableRun(
        directory,
        run_id=run_id,
        configuration={
            "case": case_name,
            "seed": seed,
            "weights": settings.track1.preliminary_scoring.model_dump(),
            "resource_lock_hashes": hashes,
            "mode": "synthetic-offline-mock",
            "workflow_version": 1,
            "code_sha256": code_digest.hexdigest(),
        },
    )
    if (
        psutil.virtual_memory().available < 512 * 1024**2
        or psutil.disk_usage(directory).free < 1024**3
    ):
        raise RuntimeError("Insufficient live memory or disk for dry run")

    def prepare(output):
        case = generate_case(case_name, output)
        path = output / "preflight.parquet"
        write_variants_parquet(parse_vcf(Path(str(case.vcf_resource))), path, batch_size=256)
        specification = GenomicInput(
            genome_build="GRCh38",
            annotation_build="GRCh38",
            normalized_biallelic=True,
            reference_checksum=hashlib.sha256(b"synthetic-reference-not-a-genome").hexdigest(),
            transcript_release="synthetic-1",
            sample_ids=[person.id for person in case.pedigree.individuals],
            pedigree=case.pedigree,
            par_intervals={"X": [], "Y": []},
            allow_missing_annotations=case_name not in EDGE_CASES
            or case_name == "annotation-missing",
        )
        result = validate_parquet(path, specification)
        atomic_json(output / "preflight.json", result.model_dump(mode="json"))

    runner.stage("preflight", prepare)
    if interrupt_after == "preflight":
        raise InterruptedError("Synthetic interruption after committed preflight")

    def pipeline(output):
        case = generate_case(case_name, output)
        workflow = Track1EvidenceWorkflow(
            case_name="de-novo",
            backend=MockLLMBackend(structured_responses=phase3_mock_decisions()),
            settings=settings,
            run_directory=output,
            run_id=run_id,
        )
        workflow.case = case
        workflow.run()

    pipeline_path = runner.stage("pipeline", pipeline)
    if interrupt_after == "pipeline":
        raise InterruptedError("Synthetic interruption after committed pipeline")

    def report(output):
        result = write_report(
            pipeline_path / "candidate_membership.duckdb",
            run_id,
            output / "research_report.json",
            salt=secrets.token_bytes(32),
            citations=["Synthetic HPO fixture, 2026-09-03; MIT; no biomedical validity."],
        )

        if not result.critic.passed:
            raise RuntimeError("Research report failed evidence critic checks")

    report_path = runner.stage("report", report)
    runner.complete()
    return report_path / "research_report.json"


def phase4_run(directory: Path, **configuration) -> Path:
    """Hold an exclusive local run lock across all stage transactions."""
    import fcntl

    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".run.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Run already has an active writer") from exc
        try:
            return _phase4_run(directory, **configuration)
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
