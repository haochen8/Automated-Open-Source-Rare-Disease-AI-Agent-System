"""Path-scoped, explicitly authorized local rehearsal over pre-annotated Parquet."""

from __future__ import annotations

import fcntl
import hashlib
import json
import secrets
from pathlib import Path
from typing import Literal

import psutil
from pydantic import BaseModel, ConfigDict, Field

from rare_disease_agent.agents.variant_agent import VariantFilteringAgent
from rare_disease_agent.config import Settings
from rare_disease_agent.llm.mock import MockLLMBackend
from rare_disease_agent.ranking.persistent import rank_persistent
from rare_disease_agent.ranking.scoring import PreliminaryRanker
from rare_disease_agent.reporting.provenance import software_identity
from rare_disease_agent.reporting.research import write_report
from rare_disease_agent.resource_management import atomic_json, sha256
from rare_disease_agent.resource_management.hpo import IndexedPhenotypeStore
from rare_disease_agent.tools.inheritance.evaluator import InheritanceEvaluator
from rare_disease_agent.tools.phenotype.tools import PhenotypeToolbox
from rare_disease_agent.tools.variants.agent_tools import VariantToolbox
from rare_disease_agent.tools.variants.preflight import GenomicInput, validate_parquet
from rare_disease_agent.workflows.mock_strategy import phase3_mock_decisions
from rare_disease_agent.workflows.recovery import RestartableRun
from rare_disease_agent.workflows.track1_filtering import VariantFilteringWorkflow


class LocalAuthorization(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    confirmed_local_research_use: Literal[True]
    input_path: Path
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_directory: Path


def _outside_git(path: Path) -> None:
    if any((ancestor / ".git").exists() for ancestor in (path, *path.parents)):
        raise ValueError("Authorized input and derived output must be outside every Git checkout")


def authorized_dry_run(
    *,
    authorization: LocalAuthorization,
    specification: GenomicInput,
    hpo_directory: Path,
    hpo_terms: list[str],
    run_id: str,
    seed: int = 17,
) -> Path:
    """The operator must explicitly confirm the exact input and output scope before calling.

    This entry point never annotates, downloads, submits or invokes a live LLM.
    Inputs must already have been normalized/annotated by an authorized process.
    """
    source = authorization.input_path.resolve(strict=True)
    output = authorization.output_directory.resolve()
    _outside_git(source)
    _outside_git(output)
    if source.is_relative_to(output):
        raise ValueError("Source cannot reside inside the run output directory")
    if sha256(source) != authorization.input_sha256:
        raise ValueError("Authorized input checksum mismatch")
    if not 1 <= len(hpo_terms) <= 500:
        raise ValueError("Phenotype count outside supported bounds")
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    if (
        psutil.virtual_memory().available < 768 * 1024**2
        or psutil.disk_usage(output).free < 2 * 1024**3
    ):
        raise RuntimeError("Insufficient live memory or disk for authorized rehearsal")
    with (output / ".run.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        store = IndexedPhenotypeStore(hpo_directory)
        toolbox = None
        try:
            settings = Settings()
            software = software_identity()
            configuration = {
                "input_sha256": authorization.input_sha256,
                "context_sha256": hashlib.sha256(
                    specification.model_dump_json().encode()
                ).hexdigest(),
                "hpo_terms_sha256": hashlib.sha256(json.dumps(hpo_terms).encode()).hexdigest(),
                "resource_lock_hash": sha256(hpo_directory / "manifest.json"),
                "weights": settings.track1.preliminary_scoring.model_dump(),
                "seed": seed,
                "software": software.model_dump(),
                "mode": "authorized-local-mock",
                "schema_version": 1,
            }
            runner = RestartableRun(output, run_id=run_id, configuration=configuration)

            def preflight(path):
                result = validate_parquet(source, specification)
                atomic_json(path / "validation.json", result.model_dump(mode="json"))

            runner.stage("preflight", preflight)
            phenotype = PhenotypeToolbox(
                store, patient_hpo=hpo_terms, run_id=run_id, software=software
            )
            if phenotype.normalized.invalid_ids or phenotype.normalized.unknown_ids:
                raise ValueError("Unresolved patient phenotype identifiers")

            def pipeline(path):
                nonlocal toolbox
                toolbox = VariantToolbox(
                    source,
                    run_id=run_id,
                    membership_database=path / "candidate_membership.duckdb",
                    phenotype_toolbox=phenotype,
                    inheritance_evaluator=InheritanceEvaluator(
                        specification.pedigree, run_id=run_id, software=software
                    ),
                )
                try:
                    filtered = VariantFilteringWorkflow(
                        parquet_path=source,
                        agent=VariantFilteringAgent(
                            MockLLMBackend(structured_responses=phase3_mock_decisions())
                        ),
                        run_directory=path,
                        config=settings.agents.variant_filtering.model_copy(
                            update={
                                "max_iterations": 16,
                                "max_tool_calls": 24,
                                "minimum_candidate_count": 1,
                            }
                        ),
                        run_id=run_id,
                        toolbox=toolbox,
                    ).run()
                    if filtered.metrics.stop_reason != "evidence_ready_for_ranking":
                        raise RuntimeError(
                            "Evidence plan did not complete; inspect the local audit"
                        )
                    ranker = PreliminaryRanker(
                        weights=settings.track1.preliminary_scoring.model_dump(),
                        run_id=run_id,
                        software=software,
                    )
                    rank_persistent(
                        toolbox,
                        ranker,
                        filtered.state.current_branch,
                        annotation_version=specification.transcript_release,
                    )
                    toolbox.evidence_store.export(path / "evidence.parquet")
                    atomic_json(path / "provenance.json", configuration)
                finally:
                    toolbox.membership.close()
                    toolbox = None

            pipeline_path = runner.stage("pipeline", pipeline)

            def report(path):
                manifest = json.loads((hpo_directory / "manifest.json").read_text())
                result = write_report(
                    pipeline_path / "candidate_membership.duckdb",
                    run_id,
                    path / "research_report.json",
                    salt=secrets.token_bytes(32),
                    citations=[
                        f"{lock['release']}: {lock['citation']}; {lock['license_reference']}"
                        for lock in manifest["locks"]
                    ],
                )
                if not result.critic.passed:
                    raise RuntimeError("Research report failed evidence critic checks")

            report_path = runner.stage("report", report)
            runner.complete()
            return report_path / "research_report.json"
        finally:
            if toolbox:
                toolbox.membership.close()
            store.close()
            fcntl.flock(handle, fcntl.LOCK_UN)
