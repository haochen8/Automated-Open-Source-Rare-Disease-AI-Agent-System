"""Private end-to-end Phase 5 rehearsal over a previously verified annotation subset.

No downloads, new annotation, model calls, clinical interpretation or submission occur here.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import re
import resource
import time
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

import duckdb
import psutil
from pydantic import BaseModel, ConfigDict, Field

from rare_disease_agent.resource_management import atomic_json, sha256
from rare_disease_agent.resource_management.hpo import IndexedPhenotypeStore
from rare_disease_agent.tools.inheritance.schemas import Individual, Pedigree
from rare_disease_agent.tools.phenotype.normalize import normalize_hpo_terms
from rare_disease_agent.tools.variants.ingest import ingest_vep, verify_split_genotypes
from rare_disease_agent.tools.variants.preflight import GenomicInput
from rare_disease_agent.workflows.authorized import (
    LocalAuthorization,
    _outside_git,
    authorized_dry_run,
)
from rare_disease_agent.workflows.phase5_diagnostics import (
    Phase5IdentityMismatch,
    Phase5StageIntegrityError,
    describe_mismatch,
)
from rare_disease_agent.workflows.recovery import (
    CommittedStageIntegrityError,
    RestartableRun,
    RunIdentityMismatch,
)


class Phase5Input(BaseModel):
    model_config = ConfigDict(extra="forbid")
    original_vcf: Path
    original_index: Path
    phenotype_docx: Path
    annotation_rehearsal: Path
    hpo_directory: Path
    output: Path
    confirmed_affected_sample: bool
    confirmed_local_research_use: bool
    run_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    dataset_revision: str = "unknown-local-user-supplied-files"


def extract_explicit_hpo(path: Path) -> list[str]:
    """Extract only explicit positive table identifiers, never infer terms from clinical prose."""
    with ZipFile(path) as archive:
        if (
            len(archive.infolist()) > 500
            or sum(x.file_size for x in archive.infolist()) > 16 * 1024**2
        ):
            raise ValueError("Phenotype document exceeds work budget")
        raw = archive.read("word/document.xml")
    if b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
        raise ValueError("Unsupported phenotype XML declarations")
    root = ElementTree.fromstring(raw)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    all_text = " ".join(node.text or "" for node in root.findall(".//w:t", ns))
    all_terms = set(re.findall(r"\bHP:\d{7}\b", all_text))
    table_terms = set()
    for row in root.findall(".//w:tbl/w:tr", ns):
        text = " ".join(node.text or "" for node in row.findall(".//w:t", ns))
        terms = set(re.findall(r"\bHP:\d{7}\b", text))
        if terms and re.search(r"\b(absent|negative|denied|excluded|not present)\b", text, re.I):
            raise ValueError("Explicit phenotype polarity requires operator review")
        table_terms.update(terms)
    if not 1 <= len(table_terms) <= 500 or table_terms != all_terms:
        raise ValueError("Explicit phenotype table identifiers require review")
    return sorted(table_terms)


def code_checksum() -> str:
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for source in sorted(root.rglob("*.py")):
        digest.update(str(source.relative_to(root)).encode())
        digest.update(source.read_bytes())
    return digest.hexdigest()


def _run(spec: Phase5Input, *, interrupt_after: str | None = None) -> dict:
    started = time.monotonic()
    if not spec.confirmed_local_research_use:
        raise ValueError("Explicit local authorization required")
    for path in (
        spec.original_vcf,
        spec.original_index,
        spec.phenotype_docx,
        spec.annotation_rehearsal,
        spec.hpo_directory,
        spec.output,
    ):
        _outside_git(path.resolve())
    if (
        psutil.disk_usage(spec.output).free < 25 * 1024**3
        or psutil.virtual_memory().available < 1024**3
    ):
        raise RuntimeError("Insufficient live resources for the private dry run")
    inputs = {
        "challenge_track1_vcf": spec.original_vcf,
        "challenge_index": spec.original_index,
        "challenge_phenotype": spec.phenotype_docx,
    }
    fingerprints = {
        name: {
            "sha256": sha256(path),
            "bytes": path.stat().st_size,
            "mtime_ns": path.stat().st_mtime_ns,
        }
        for name, path in inputs.items()
    }
    rehearsal = spec.annotation_rehearsal
    prior = json.loads((rehearsal / "run_manifest.json").read_text())
    if prior["status"] != "completed":
        raise ValueError("Annotation rehearsal does not match original input")
    if prior["source_sha256"] != fingerprints["challenge_track1_vcf"]["sha256"]:
        raise Phase5IdentityMismatch(["annotation_source"])
    integrity = json.loads((rehearsal / "integrity_manifest.json").read_text())
    for name, digest in integrity.items():
        if Path(name).name != name or sha256(rehearsal / name) != digest:
            raise ValueError("Annotation rehearsal integrity failed")
    required = {
        "subset.vcf",
        "normalized.vcf",
        "annotated.vcf",
        "normalization.receipt.json",
        "annotation.receipt.json",
    }
    if not required <= integrity.keys():
        raise ValueError("Annotation integrity manifest is incomplete")
    for name, incoming, outgoing in (
        ("normalization.receipt.json", "subset.vcf", "normalized.vcf"),
        ("annotation.receipt.json", "normalized.vcf", "annotated.vcf"),
    ):
        receipt = json.loads((rehearsal / name).read_text())
        if (
            receipt["input_sha256"] != integrity[incoming]
            or receipt["output_sha256"] != integrity[outgoing]
            or receipt["returncode"] != 0
        ):
            raise ValueError("Annotation stage linkage failed")
    configuration = {
        "inputs": fingerprints,
        "rehearsal_integrity_sha256": sha256(rehearsal / "integrity_manifest.json"),
        "hpo_manifest_sha256": sha256(spec.hpo_directory / "manifest.json"),
        "code_sha256": code_checksum(),
        "scope": "previously annotated bounded subset only",
        "specification": spec.model_dump(mode="json"),
    }
    try:
        runner = RestartableRun(spec.output, run_id=spec.run_id, configuration=configuration)
    except RunIdentityMismatch as exc:
        raise describe_mismatch(exc, configuration) from None

    def prepare(path):
        metrics = verify_split_genotypes(rehearsal / "subset.vcf", rehearsal / "normalized.vcf")
        hpo = extract_explicit_hpo(spec.phenotype_docx)
        store = IndexedPhenotypeStore(spec.hpo_directory)
        try:
            normalized_hpo = normalize_hpo_terms(hpo, store.ontology)
            if normalized_hpo.invalid_ids or normalized_hpo.unknown_ids:
                raise ValueError("Unresolved explicit phenotype identifiers")
            atomic_json(path / "normalized_phenotype.json", normalized_hpo.model_dump(mode="json"))
        finally:
            store.close()
        atomic_json(
            path / "phenotype.json",
            {"explicit_hpo_ids": hpo, "source": fingerprints["challenge_phenotype"]},
        )
        atomic_json(path / "reconciliation.json", metrics)
        atomic_json(
            path / "annotation_provenance.json",
            {
                name: json.loads((rehearsal / name).read_text())
                for name in ("normalization.receipt.json", "annotation.receipt.json")
            },
        )
        atomic_json(path / "run_manifest.json", configuration)

    prepare_path = runner.stage("prepare", prepare)
    if interrupt_after == "prepare":
        raise InterruptedError("Requested interruption after committed preparation")
    imported = runner.stage(
        "ingest",
        lambda path: ingest_vep(
            rehearsal / "annotated.vcf", rehearsal / "normalized.vcf", path / "tables"
        ),
    )
    table_dir = imported / "tables"
    import_metrics = json.loads((table_dir / "manifest.json").read_text())
    hpo = json.loads((prepare_path / "phenotype.json").read_text())["explicit_hpo_ids"]
    # The prepared receipt includes the pinned reference checksum in its configuration.
    reference_checksum = prior["authorized_scope"]["operations"][0]["reference_sha256"]
    context = GenomicInput(
        genome_build="GRCh38",
        annotation_build="GRCh38",
        normalized_biallelic=True,
        reference_checksum=reference_checksum,
        transcript_release="Ensembl-VEP-116-GRCh38",
        sample_ids=["sample_1"],
        pedigree=Pedigree(
            proband_id="sample_1",
            individuals=[
                Individual(
                    id="sample_1",
                    affected=True if spec.confirmed_affected_sample else None,
                    sex="unknown",
                )
            ],
        ),
        par_intervals={"X": [], "Y": []},
        allow_missing_annotations=True,
    )

    def execute(path):
        authorized_dry_run(
            authorization=LocalAuthorization(
                confirmed_local_research_use=True,
                input_path=table_dir / "variants.parquet",
                input_sha256=sha256(table_dir / "variants.parquet"),
                output_directory=path / "workflow",
            ),
            specification=context,
            hpo_directory=spec.hpo_directory,
            hpo_terms=hpo,
            run_id=spec.run_id,
            phase5_strategies=True,
        )

    pipeline = runner.stage("analysis", execute) / "workflow" / "pipeline"

    def report(path):
        connection = duckdb.connect(str(pipeline / "candidate_membership.duckdb"), read_only=True)
        try:
            connection.execute("SET memory_limit='256MB'")
            connection.sql("SELECT * FROM ranked_evidence ORDER BY mode, rank").write_parquet(
                str(path / "private_candidate_ranking.parquet")
            )
            connection.sql("SELECT * FROM evidence").write_parquet(
                str(path / "private_candidate_features.parquet")
            )
            connection.sql("SELECT * FROM candidate_membership").write_parquet(
                str(path / "private_branch_membership.parquet")
            )
            branches = dict(
                connection.execute(
                    "SELECT b.branch, count(m.variant_id) FILTER (WHERE m.active) "
                    "FROM candidate_branches b LEFT JOIN candidate_membership m "
                    "ON b.run_id=m.run_id AND b.branch=m.branch "
                    "GROUP BY b.branch ORDER BY b.branch"
                ).fetchall()
            )
            modes = dict(
                connection.execute(
                    "SELECT mode, count(*) FROM ranked_evidence GROUP BY mode ORDER BY mode"
                ).fetchall()
            )
            atomic_json(path / "branch_metrics.json", branches)
            atomic_json(
                path / "inheritance_summary.json",
                {
                    "parents": "not supplied",
                    "sex": "unknown",
                    "affected_status": "confirmed" if spec.confirmed_affected_sample else "unknown",
                    "sample_phenotype_linkage_confirmed": spec.confirmed_affected_sample,
                    "de_novo": "unconfirmed without parental genotypes",
                    "phase": "unconfirmed without parental/phase-set evidence",
                },
            )
            atomic_json(
                path / "phenotype_summary.json",
                {
                    "explicit_input_count": len(hpo),
                    "submitted_explicit_terms": hpo,
                    "normalization": json.loads(
                        (prepare_path / "normalized_phenotype.json").read_text()
                    ),
                    "resource_manifest_sha256": configuration["hpo_manifest_sha256"],
                },
            )
            source_counts = json.loads((prepare_path / "reconciliation.json").read_text())
            metrics = {
                "status": "completed_bounded_dry_run",
                "scope": configuration["scope"],
                **source_counts,
                "imported_alleles": import_metrics["alleles"],
                "gene_level_candidates": import_metrics["variants"],
                "consequence_entries_preserved": import_metrics["consequences"],
                "deferred_in_subset": import_metrics["deferred_alleles"],
                "missing_population_frequency": import_metrics["missing_population_frequency"],
                "branch_counts": branches,
                "ranking_counts": modes,
                "runtime_seconds": round(time.monotonic() - started, 2),
                "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                "whole_genome_analysis_complete": False,
                "sample_phenotype_linkage_confirmed": spec.confirmed_affected_sample,
                "causal_accuracy_assessed": False,
                "genotype_reconciliation": True,
                "source_integrity_verified": True,
            }
            atomic_json(path / "sanitized_metrics.json", metrics)
            (path / "private_research_report.md").write_text(
                "# Phase 5 bounded Track 1 research dry run\n\n"
                "The deterministic workflow completed for the previously annotated subset. "
                "This is an input-format and pipeline integration rehearsal, not a whole-genome "
                "ranking or causal diagnosis. Records outside this scope remain unprocessed and "
                "recoverable in the unchanged original VCF.\n\n"
                "Every CSQ entry is retained. Rankings use one candidate per source allele and "
                "gene, with the highest configured consequence score for that gene. Canonical "
                "allele mapping and all transcripts are available in the ingestion tables.\n\n"
                "Missing population frequency stays unknown. Unknown gene associations are "
                "retained by rescue branches. Parents and sex are unknown; de novo and phase "
                "are unconfirmed. Numeric features are heuristic, not probabilities.\n\n"
                "If the sample's affected status is unconfirmed, phenotype scores are conditional "
                "on the supplied document belonging to the analyzed sample. Inheritance fits are "
                "capped as uncertain. This linkage must be verified before interpretation.\n\n"
                "Six fixed ranking strategies are stored privately alongside individual evidence, "
                "branch membership, provenance and the bounded JSON critic report. No official "
                "submission, remote model, new download or clinical recommendation occurred.\n"
            )
        finally:
            connection.close()

    report_path = runner.stage("deliverables", report)
    for name, path in inputs.items():
        if sha256(path) != fingerprints[name]["sha256"]:
            raise ValueError("Original input changed during dry run")
    runner.complete()
    atomic_json(
        spec.output / "integrity_manifest.json",
        {
            "run_id": spec.run_id,
            "configuration_hash": runner.journal.configuration_hash,
            "scope": "immutable stage artifacts; supervision receipts are separate",
            "outputs": {
                f"{stage}/{name}": digest
                for stage, record in runner.journal.stages.items()
                for name, digest in record.outputs.items()
            },
        },
    )
    return json.loads((report_path / "sanitized_metrics.json").read_text())


def phase5_run(spec: Phase5Input, *, interrupt_after: str | None = None) -> dict:
    _outside_git(spec.output.resolve())
    spec.output.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (spec.output / ".run.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            return _run(spec, interrupt_after=interrupt_after)
        except Phase5IdentityMismatch as exc:
            # Reconstruct from fixed codes rather than forwarding arbitrary exception text.
            raise Phase5IdentityMismatch(exc.codes) from None
        except CommittedStageIntegrityError as exc:
            raise Phase5StageIntegrityError(exc.stage, exc.reason) from None
        except Exception as exc:
            # Do not forward patient-bearing exceptions, rows, identifiers or file paths.
            raise RuntimeError("Private Phase 5 stage failed: " + type(exc).__name__) from None
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
