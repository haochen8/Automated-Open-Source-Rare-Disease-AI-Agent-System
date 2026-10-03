"""Local truth-linked analysis of immutable Phase 5 artifacts, without reranking."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import shutil
import tempfile
from pathlib import Path
from typing import Literal

import duckdb
import psutil
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from rare_disease_agent.ranking.evaluation import ranking_metrics
from rare_disease_agent.ranking.scoring import MODE_FEATURES
from rare_disease_agent.ranking.strategies import STRATEGIES
from rare_disease_agent.resource_management import atomic_json, sha256
from rare_disease_agent.workflows.authorized import _outside_git
from rare_disease_agent.workflows.phase5 import code_checksum
from rare_disease_agent.workflows.recovery import RunJournal

TOOL_VERSION = "phase5-case-evaluation-v2"


class SourceAllele(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_row: int = Field(strict=True, ge=1)
    source_alt: int = Field(strict=True, ge=1)


class LabeledCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str = Field(min_length=1, max_length=80)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    truth_reference: str = Field(min_length=1, max_length=500)
    expected_gene_id: str = Field(min_length=1, max_length=100)
    # Empty means gene-only truth. Two alleles means BOTH are required, never alternatives.
    alleles: list[SourceAllele] = Field(default_factory=list, max_length=2)
    run_directory: Path | None = None
    unavailable_reason: (
        Literal[
            "not_run",
            "preparation_failed",
            "annotation_failed",
            "execution_failed",
            "unsupported_input",
        ]
        | None
    ) = None

    @model_validator(mode="after")
    def explicit_scope(self):
        if (self.run_directory is None) != (self.unavailable_reason is not None):
            raise ValueError("Supply a run directory or an explicit unavailable reason")
        if len(set(self.alleles)) != len(self.alleles):
            raise ValueError("Duplicate truth alleles")
        if self.expected_gene_id.upper() in {".", "UNKNOWN", "UNASSIGNED"}:
            raise ValueError("Truth requires an assigned gene identifier")
        return self


class EvaluationManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    confirmed_local_research_use: Literal[True]
    mode: str = "filtering_phenotype_inheritance"
    cases: list[LabeledCase] = Field(min_length=1, max_length=1000)

    @field_validator("mode")
    @classmethod
    def supported_mode(cls, value):
        modes = set(MODE_FEATURES) | {
            f"{strategy}_{mode}" for strategy in STRATEGIES for mode in MODE_FEATURES
        }
        if value not in modes:
            raise ValueError("Unsupported persisted ranking mode")
        return value

    @model_validator(mode="after")
    def unique_cases(self):
        if len({case.case_id for case in self.cases}) != len(self.cases):
            raise ValueError("Duplicate case identifiers")
        # Renaming the same input does not create an independent evaluation case.
        if len({case.source_sha256 for case in self.cases}) != len(self.cases):
            raise ValueError("Duplicate source inputs; evaluate each source once")
        return self


def _json(path: Path):
    with path.open("rb") as stream:
        raw = stream.read(4 * 1024**2 + 1)
    if len(raw) > 4 * 1024**2:
        raise ValueError("Evaluation metadata exceeds work budget")
    return json.loads(raw)


class _Artifacts:
    """Verify only consumed committed artifacts; never follow external journal paths."""

    def __init__(self, root: Path, source_sha256: str):
        self.root = root.resolve(strict=True)
        _outside_git(self.root)
        self.identities = {}
        journal_path = self.root / "run.json"
        self._remember(journal_path)
        self.journal = RunJournal.model_validate(_json(journal_path))
        config = self.journal.configuration
        digest = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
        if digest != self.journal.configuration_hash:
            raise ValueError("Run configuration integrity mismatch")
        if config["inputs"]["challenge_track1_vcf"]["sha256"] != source_sha256:
            raise ValueError("Truth source identity mismatch")

    def _remember(self, path):
        if path.resolve(strict=True) != path or not path.is_file():
            raise ValueError("Evaluation artifacts must be regular files without symlinks")
        self.identities[path] = sha256(path)

    def get(self, stage: str, name: str) -> Path:
        path = self.root / stage / name
        self._remember(path)
        if self.journal.stages[stage].outputs.get(name) != self.identities[path]:
            raise ValueError("Committed evaluation artifact integrity mismatch")
        return path

    def verify_unchanged(self):
        if any(
            path.resolve() != path or sha256(path) != digest
            for path, digest in self.identities.items()
        ):
            raise ValueError("Evaluation inputs changed during inspection")


def _all_rank(ranks):
    return max(ranks) if ranks and all(rank is not None for rank in ranks) else None


def _validate_ranked_candidates(db, branch):
    # Validate unrelated candidates too: losing one in a join changes retrieval order.
    for table in ("mapping", "variants"):
        if db.execute(
            f"SELECT r.variant_id FROM ranking r LEFT JOIN {table} t USING(variant_id) "
            "GROUP BY r.variant_id HAVING count(t.variant_id)<>1 LIMIT 1"
        ).fetchone():
            raise ValueError("Ranked candidates require unique mapping and variant rows")
    if db.execute("""SELECT r.variant_id FROM ranking r
        JOIN mapping m USING(variant_id) LEFT JOIN alleles a USING(allele_id)
        GROUP BY r.variant_id HAVING count(a.allele_id)<>1 LIMIT 1""").fetchone():
        raise ValueError("Ranked candidates require unique allele ledger rows")
    if db.execute(
        "SELECT 1 FROM ranking r FULL OUTER JOIN "
        "(SELECT variant_id, count(*) AS n FROM membership WHERE branch=? AND active "
        "GROUP BY variant_id) m USING(variant_id) "
        "WHERE r.variant_id IS NULL OR m.variant_id IS NULL OR m.n<>1 LIMIT 1",
        [branch],
    ).fetchone():
        raise ValueError("Ranking and active branch membership disagree")


def _candidate_detail(db, variant_id, gene, branch):
    row = db.execute(
        "SELECT rank, raw_score, features, provenance FROM ranking WHERE variant_id=?",
        [variant_id],
    ).fetchone()
    phenotype = db.execute(
        "SELECT bool_or(known) FROM evidence WHERE gene=? AND method='resnik_bma'", [gene.upper()]
    ).fetchone()[0]
    inheritance = db.execute(
        "SELECT method, score, payload FROM evidence WHERE variant_id=? "
        "AND method<>'resnik_bma' ORDER BY method LIMIT 20",
        [variant_id],
    ).fetchall()
    branches = [
        item[0]
        for item in db.execute(
            "SELECT branch FROM membership WHERE variant_id=? AND active ORDER BY branch",
            [variant_id],
        ).fetchall()
    ]
    annotation = db.execute(
        "SELECT allele_frequency IS NOT NULL, consequence IS NOT NULL FROM variants "
        "WHERE variant_id=?",
        [variant_id],
    ).fetchone()
    result = {
        "candidate_id": variant_id,
        "rank": row[0] if row else None,
        "phenotype_association_available": phenotype,
        "inheritance_evidence_available": bool(inheritance),
        "inheritance_warnings": sorted(
            {
                warning
                for _, _, payload in inheritance
                for warning in json.loads(payload).get("warnings", [])
            }
        ),
        "strong_inheritance_models": [method for method, score, _ in inheritance if score >= 0.8],
        "active_branches": branches,
        "retained_after_filtering": branch in branches,
        "rescue_member": "novel-gene-rescue" in branches,
        "rescue_required": None,
        "population_frequency_available": annotation[0],
        "consequence_available": annotation[1],
    }
    if row:
        features, provenance = json.loads(row[2]), json.loads(row[3])
        weights = provenance["parameters"]["weights"]
        included = provenance["parameters"]["included_features"]
        if not included or not set(included) <= set(
            MODE_FEATURES["filtering_phenotype_inheritance"]
        ):
            raise ValueError("Unsupported ranking feature provenance")
        total = sum(weights[name] for name in included)
        contributions = {name: features[name] * weights[name] / total for name in included}
        if not all(math.isfinite(value) for value in contributions.values()) or not math.isclose(
            sum(contributions.values()), row[1], rel_tol=0, abs_tol=0.000001
        ):
            raise ValueError("Ranking contributions do not reproduce persisted score")
        result.update(
            score=row[1], features=features, contributions=contributions, provenance=provenance
        )
    return result


def _inspect_case(case: LabeledCase, mode: str) -> dict:
    result = {
        "case_id": case.case_id,
        "source_sha256": case.source_sha256,
        "truth_reference": case.truth_reference,
        "expected_gene_id": case.expected_gene_id,
        "truth_alleles": [item.model_dump() for item in case.alleles],
        "status": case.unavailable_reason,
        "gene_rank": None,
        "gene_candidate": None,
        "allele_rank": None,
        "allele_gene_rank": None,
        "candidate_set_size": None,
        "targets": [
            {
                **item.model_dump(),
                "retained_after_preparation": None,
                "retained_after_filtering": None,
                "allele_rank": None,
                "candidate": None,
                "failure_reason": case.unavailable_reason or "run_incomplete",
            }
            for item in case.alleles
        ],
    }
    if case.run_directory is None:
        return result
    artifacts = _Artifacts(case.run_directory, case.source_sha256)
    journal = artifacts.journal
    result.update(
        status="completed"
        if journal.ended_at and "deliverables" in journal.stages
        else "incomplete",
        committed_stages=list(journal.stages),
        run_configuration_hash=journal.configuration_hash,
        pipeline_code_sha256=journal.configuration.get("code_sha256"),
        hpo_manifest_sha256=journal.configuration.get("hpo_manifest_sha256"),
    )
    if "ingest" not in journal.stages:
        artifacts.verify_unchanged()
        result["artifact_checksums"] = {
            "run.json": artifacts.identities[artifacts.root / "run.json"]
        }
        return result
    result["targets"] = []
    with duckdb.connect(
        config={
            "memory_limit": "256MB",
            "threads": 1,
            "temp_directory": "",
            "autoinstall_known_extensions": False,
            "autoload_known_extensions": False,
        }
    ) as db:
        for table, name in (
            ("alleles", "alleles"),
            ("mapping", "candidate_map"),
            ("variants", "variants"),
        ):
            path = artifacts.get("ingest", f"tables/{name}.parquet")
            db.from_parquet(str(path)).create_view(table)
        complete = result["status"] == "completed"
        if complete:
            for table, name in (
                ("ranks", "ranking"),
                ("evidence", "features"),
                ("membership", "branch_membership"),
            ):
                path = artifacts.get(
                    "deliverables",
                    f"private_candidate_{name}.parquet"
                    if table != "membership"
                    else "private_branch_membership.parquet",
                )
                db.from_parquet(str(path)).create_view(table)
            # Bind selection as a value, never as caller-provided SQL.
            db.execute("CREATE TEMP TABLE ranking AS SELECT * FROM ranks WHERE mode=?", [mode])
            count, distinct_count, runs, rank_count, lowest, highest = db.execute(
                "SELECT count(*), count(DISTINCT variant_id), count(DISTINCT run_id), "
                "count(DISTINCT rank), min(rank), max(rank) FROM ranking"
            ).fetchone()
            if (
                count == 0
                or count != distinct_count
                or runs != 1
                or (rank_count, lowest, highest) != (count, 1, count)
            ):
                raise ValueError("Missing or ambiguous ranking mode")
            if db.execute("SELECT run_id FROM ranking LIMIT 1").fetchone()[0] != journal.run_id:
                raise ValueError("Ranking run identity mismatch")
            for table in ("evidence", "membership"):
                if db.execute(
                    f"SELECT count(*) FROM {table} WHERE run_id IS NULL OR run_id<>?",
                    [journal.run_id],
                ).fetchone()[0]:
                    raise ValueError("Mixed evidence or membership run identity")
            branch = "conservative" if mode.startswith("conservative_") else "ensemble"
            _validate_ranked_candidates(db, branch)
            result["candidate_set_size"] = count
            db.execute("""CREATE TEMP VIEW gene_ranks AS
                SELECT m.gene_id, row_number() OVER (ORDER BY min(r.rank), m.gene_id) rank
                FROM ranking r JOIN mapping m USING(variant_id)
                WHERE upper(m.gene_id) NOT IN ('UNKNOWN', 'UNASSIGNED', '.', '')
                GROUP BY m.gene_id""")
            db.execute("""CREATE TEMP VIEW allele_ranks AS
                SELECT m.allele_id, row_number() OVER (ORDER BY min(r.rank), m.allele_id) rank
                FROM ranking r JOIN mapping m USING(variant_id) GROUP BY m.allele_id""")
            gene = db.execute(
                "SELECT rank FROM gene_ranks WHERE gene_id=?", [case.expected_gene_id]
            ).fetchone()
            result["gene_rank"] = gene[0] if gene else None
            gene_candidate = db.execute(
                "SELECT r.variant_id, m.gene_symbol FROM ranking r "
                "JOIN mapping m USING(variant_id) "
                "WHERE m.gene_id=? ORDER BY r.rank LIMIT 1",
                [case.expected_gene_id],
            ).fetchone()
            if gene_candidate:
                result["gene_candidate"] = _candidate_detail(
                    db, gene_candidate[0], gene_candidate[1] or case.expected_gene_id, branch
                )
        for truth in case.alleles:
            observed = db.execute(
                "SELECT allele_id, disposition FROM alleles WHERE source_row=? AND source_alt=?",
                [str(truth.source_row), str(truth.source_alt)],
            ).fetchall()
            if len(observed) > 1:
                raise ValueError("Ambiguous source allele mapping")
            target = {
                **truth.model_dump(),
                "retained_after_preparation": bool(observed),
                "retained_after_filtering": None,
                "allele_rank": None,
                "candidate": None,
                "failure_reason": "outside_ingested_subset",
            }
            if observed:
                allele_id, disposition = observed[0]
                target["failure_reason"] = (
                    "deferred_contig_context"
                    if disposition != "included"
                    else "expected_gene_not_mapped"
                )
                candidates = db.execute(
                    "SELECT variant_id, gene_symbol FROM mapping WHERE allele_id=? AND gene_id=?",
                    [allele_id, case.expected_gene_id],
                ).fetchall()
                if len(candidates) > 1:
                    raise ValueError("Ambiguous allele/gene candidate")
                if complete:
                    rank = db.execute(
                        "SELECT rank FROM allele_ranks WHERE allele_id=?", [allele_id]
                    ).fetchone()
                    target["allele_rank"] = rank[0] if rank else None
                if candidates:
                    variant_id, symbol = candidates[0]
                    if complete:
                        branch = "conservative" if mode.startswith("conservative_") else "ensemble"
                        detail = _candidate_detail(
                            db, variant_id, symbol or case.expected_gene_id, branch
                        )
                        target.update(
                            candidate=detail,
                            retained_after_filtering=detail["retained_after_filtering"],
                        )
                        target["failure_reason"] = (
                            "ranked" if detail["rank"] is not None else "filtered_out"
                        )
                        if detail["retained_after_filtering"] != (detail["rank"] is not None):
                            raise ValueError("Ranking and active branch membership disagree")
                    else:
                        target["failure_reason"] = "run_incomplete"
            result["targets"].append(target)
        result["allele_rank"] = _all_rank([item["allele_rank"] for item in result["targets"]])
        result["allele_gene_rank"] = _all_rank(
            [item["candidate"]["rank"] if item["candidate"] else None for item in result["targets"]]
        )
    artifacts.verify_unchanged()
    result["artifact_checksums"] = {
        str(path.relative_to(artifacts.root)): digest
        for path, digest in artifacts.identities.items()
    }
    return result


def evaluate_cases(manifest_path: Path, output: Path) -> None:
    """Publish private JSON/TSV atomically; inputs and completed runs remain unchanged."""
    if any(
        os.environ.get(name, "").lower() in {"true", "1", "yes"}
        for name in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2")
    ):
        raise RuntimeError("External tracing must be disabled for local research workflows")
    manifest_path = manifest_path.resolve(strict=True)
    output = output.resolve()
    for path in (manifest_path, output):
        _outside_git(path)
    if output.exists() or manifest_path.is_relative_to(output):
        raise ValueError("Evaluation requires a fresh separate output directory")
    manifest_hash = sha256(manifest_path)
    manifest = EvaluationManifest.model_validate(_json(manifest_path))
    if psutil.virtual_memory().available < 1024**3:
        raise RuntimeError("Insufficient live memory for private case evaluation")
    for case in manifest.cases:
        if case.run_directory is not None:
            if not case.run_directory.is_absolute():
                raise ValueError("Run directories must be absolute")
            root = case.run_directory.resolve()
            _outside_git(root)
            if output.is_relative_to(root) or root.is_relative_to(output):
                raise ValueError("Evaluation must not modify an existing run")
    cases = [_inspect_case(case, manifest.mode) for case in manifest.cases]
    allele_cases = [case for case in cases if case["truth_alleles"]]
    result = {
        "schema_version": 1,
        "tool_version": TOOL_VERSION,
        "mode": manifest.mode,
        "manifest_sha256": manifest_hash,
        "evaluator_code_sha256": code_checksum(),
        "cases": cases,
        "case_count": len(cases),
        "allele_labeled_case_count": len(allele_cases),
        "metrics": {
            "gene": ranking_metrics([case["gene_rank"] for case in cases]),
            "allele": ranking_metrics([case["allele_rank"] for case in allele_cases])
            if allele_cases
            else None,
            "allele_gene": ranking_metrics([case["allele_gene_rank"] for case in allele_cases])
            if allele_cases
            else None,
        },
        "limitations": [
            "Conditional on supplied labels and bounded run scope; not whole-case coverage.",
            "Unavailable and incomplete cases count as retrieval misses; "
            "gene-only truth is excluded from allele metrics.",
            "Two labeled alleles require both; their retrieval rank is the worse rank.",
            "Gene and allele ranks deduplicate the persisted candidate order by first appearance.",
            "Rescue membership does not establish rescue necessity; "
            "strong fits do not establish unsupported calls.",
            "Source ordinals are operator-reviewed truth mappings, "
            "not an automatic coordinate resolver.",
        ],
    }
    if sha256(manifest_path) != manifest_hash:
        raise ValueError("Truth manifest changed during evaluation")
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    staging = Path(tempfile.mkdtemp(prefix=".case-evaluation-", dir=output.parent))
    try:
        atomic_json(staging / "evaluation.json", result)
        with (staging / "cases.tsv").open("w", newline="") as stream:
            columns = [
                "case_id",
                "expected_gene_id",
                "status",
                "candidate_set_size",
                "gene_rank",
                "gene_candidate",
                "allele_rank",
                "allele_gene_rank",
                "targets",
            ]
            writer = csv.DictWriter(stream, fieldnames=columns, delimiter="\t")
            writer.writeheader()
            for case in cases:
                row = {name: case[name] for name in columns}
                row["targets"] = json.dumps(row["targets"], sort_keys=True)
                row["gene_candidate"] = json.dumps(row["gene_candidate"], sort_keys=True)
                writer.writerow(row)
        staging.rename(output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
