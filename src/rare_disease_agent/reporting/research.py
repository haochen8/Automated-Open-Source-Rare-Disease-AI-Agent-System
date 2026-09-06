"""Bounded deterministic evidence critic and allow-listed redacted local report."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable
from pathlib import Path
from typing import Literal

import duckdb
from pydantic import BaseModel, ConfigDict, Field

from rare_disease_agent.ranking.schemas import VariantRankingFeatures
from rare_disease_agent.resource_management import atomic_json
from rare_disease_agent.tools.inheritance.schemas import InheritanceModel

WARNING = "Research use only. Not for clinical use, diagnosis, treatment, or official submission."
LIMITATIONS = [
    "Scores are heuristic fits, not diagnostic probabilities.",
    "Synthetic evaluation does not establish clinical accuracy.",
    "Missing evidence and unconfirmed phase remain uncertain.",
]


class Reinspection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    operation: Literal["completeness", "provenance", "rescue", "uncertainty"]


class CriticFacts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    complete_evidence: bool
    provenance_matches: bool
    rescue_survives: bool
    uncertain_language_present: bool
    competing_strong_models: int = Field(ge=0)


class CriticResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    findings: tuple[str, ...]
    reinspections: int = Field(ge=0, le=2)
    passed: bool


def critique(
    facts: CriticFacts,
    reinspect: Callable[[Reinspection], CriticFacts] | None = None,
    *,
    max_reinspections: int = 2,
) -> CriticResult:
    if not 0 <= max_reinspections <= 2:
        raise ValueError("Critic budget exceeds boundary")
    operations = {
        "complete_evidence": "completeness",
        "provenance_matches": "provenance",
        "rescue_survives": "rescue",
        "uncertain_language_present": "uncertainty",
    }
    calls = 0
    while calls < max_reinspections and reinspect:
        missing = next(
            (operation for field, operation in operations.items() if not getattr(facts, field)),
            None,
        )
        if missing is None:
            break
        facts = CriticFacts.model_validate(reinspect(Reinspection(operation=missing)))
        calls += 1
    findings = [operation for field, operation in operations.items() if not getattr(facts, field)]
    passed = not findings
    if facts.competing_strong_models:
        findings.append("Multiple strong inheritance models require research review.")
    return CriticResult(findings=tuple(findings), reinspections=calls, passed=passed)


class ReportCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_ref: str = Field(pattern=r"^candidate-[0-9a-f]{16}$")
    rank: int = Field(ge=1)
    score: float = Field(ge=0, le=1)
    features: VariantRankingFeatures
    phenotype_method: Literal["resnik_bma"] = "resnik_bma"
    phenotype_data_version: str
    inheritance: dict[InheritanceModel, float]
    uncertain: bool
    phenotype_association_count: int = Field(ge=0)
    matched_phenotype_count: int = Field(ge=0)
    unmatched_phenotype_count: int = Field(ge=0)
    inheritance_quality: dict[InheritanceModel, bool]
    uncertainty_notes: list[str]


class ResearchReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    warning: Literal[
        "Research use only. Not for clinical use, diagnosis, treatment, or official submission."
    ] = WARNING
    candidates: list[ReportCandidate] = Field(max_length=20)
    critic: CriticResult
    limitations: list[str]
    release_citations: list[str]
    total_ranked: int


def inspect_facts(connection, run_id: str) -> CriticFacts:
    missing = connection.execute(
        """SELECT count(*) FROM ranked_evidence r
        WHERE r.run_id=? AND r.mode='filtering_phenotype_inheritance' AND (
        NOT EXISTS(SELECT 1 FROM evidence e WHERE e.run_id=r.run_id AND e.gene=r.gene AND
e.method='resnik_bma') OR
        NOT EXISTS(SELECT 1 FROM evidence e WHERE e.run_id=r.run_id AND
e.variant_id=r.variant_id AND e.method<>'resnik_bma'))""",
        [run_id],
    ).fetchone()[0]
    required_models_missing = connection.execute(
        "SELECT count(*) FROM ranked_evidence r WHERE r.run_id=? "
        "AND r.mode='filtering_phenotype_inheritance' AND "
        "(SELECT count(DISTINCT method) FROM evidence e WHERE e.run_id=r.run_id "
        "AND e.variant_id=r.variant_id AND method IN "
        "('de_novo','autosomal_dominant','homozygous_recessive','autosomal_recessive','x_linked'))<5",
        [run_id],
    ).fetchone()[0]
    ranked_count = connection.execute(
        "SELECT count(*) FROM ranked_evidence WHERE run_id=?", [run_id]
    ).fetchone()[0]
    rescue_exists = connection.execute(
        "SELECT count(*) FROM candidate_branches WHERE run_id=? AND branch='novel-gene-rescue'",
        [run_id],
    ).fetchone()[0]
    mismatched = connection.execute(
        (
            "SELECT count(*) FROM evidence WHERE run_id<>? OR "
            "json_extract_string(payload, '$.provenance.run_id') IS DISTINCT FROM run_id OR "
            "json_extract_string(payload, '$.provenance.data_version') "
            "IS DISTINCT FROM data_version"
        ),
        [run_id],
    ).fetchone()[0]
    rescue_lost = connection.execute(
        (
            "SELECT count(*) FROM candidate_membership m WHERE m.run_id=? AND "
            "m.branch='novel-gene-rescue' AND NOT EXISTS(SELECT 1 FROM ranked_evidence r"
            " WHERE r.run_id=m.run_id AND r.variant_id=m.variant_id AND "
            "r.mode='filtering_phenotype_inheritance')"
        ),
        [run_id],
    ).fetchone()[0]
    competing = connection.execute(
        (
            "SELECT count(*) FROM (SELECT variant_id FROM evidence WHERE run_id=? AND "
            "method IN ('de_novo','autosomal_dominant','x_linked','autosomal_recessive')"
            " AND score>=0.8 GROUP BY variant_id HAVING count(*)>1)"
        ),
        [run_id],
    ).fetchone()[0]
    return CriticFacts(
        complete_evidence=missing == 0 and required_models_missing == 0 and ranked_count > 0,
        provenance_matches=mismatched == 0,
        rescue_survives=rescue_lost == 0 and rescue_exists == 1,
        uncertain_language_present=any("uncertain" in note.lower() for note in LIMITATIONS),
        competing_strong_models=competing,
    )


def write_report(
    database: Path, run_id: str, output: Path, *, salt: bytes, citations: list[str]
) -> ResearchReport:
    if len(salt) < 16:
        raise ValueError("Report pseudonym salt too short")
    with duckdb.connect(str(database), read_only=True) as connection:
        critic = critique(
            inspect_facts(connection, run_id), lambda request: inspect_facts(connection, run_id)
        )
        candidates = []
        for variant_id, gene, rank, score, features in connection.execute(
            (
                "SELECT variant_id, gene, rank, raw_score, features FROM ranked_evidence "
                "WHERE run_id=? AND mode='filtering_phenotype_inheritance' ORDER BY rank "
                "LIMIT 20"
            ),
            [run_id],
        ).fetchall():
            inheritance = dict(
                connection.execute(
                    (
                        "SELECT method, max(score) FROM evidence WHERE run_id=? AND variant_id=? "
                        "GROUP BY method"
                    ),
                    [run_id, variant_id],
                ).fetchall()
            )
            version = connection.execute(
                (
                    "SELECT data_version FROM evidence WHERE run_id=? AND gene=? AND "
                    "method='resnik_bma' LIMIT 1"
                ),
                [run_id, gene],
            ).fetchone()
            phenotype_row = connection.execute(
                "SELECT payload FROM evidence WHERE run_id=? AND gene=? AND method='resnik_bma'",
                [run_id, gene],
            ).fetchone()
            phenotype_payload = json.loads(phenotype_row[0]) if phenotype_row else {}
            inheritance_payloads = [
                json.loads(row[0])
                for row in connection.execute(
                    "SELECT payload FROM evidence WHERE run_id=? AND variant_id=? "
                    "AND method<>'resnik_bma'",
                    [run_id, variant_id],
                ).fetchall()
            ]
            uncertain = any(0 < value < 0.8 for value in inheritance.values())
            uncertain = uncertain or any(
                not item["quality_checks_passed"] for item in inheritance_payloads
            )
            notes = (
                ["Inheritance evidence remains uncertain; inspect local evidence tables."]
                if uncertain
                else []
            )
            if phenotype_payload.get("association_count", 0) == 0:
                notes.append("No known gene association; absence is not exclusion evidence.")
            candidates.append(
                ReportCandidate(
                    candidate_ref="candidate-"
                    + hmac.new(salt, variant_id.encode(), hashlib.sha256).hexdigest()[:16],
                    rank=rank,
                    score=score,
                    features=json.loads(features),
                    phenotype_data_version=version[0] if version else "missing",
                    inheritance=inheritance,
                    uncertain=uncertain,
                    phenotype_association_count=phenotype_payload.get("association_count", 0),
                    matched_phenotype_count=len(phenotype_payload.get("matched_terms", [])),
                    unmatched_phenotype_count=len(
                        phenotype_payload.get("unmatched_patient_terms", [])
                    ),
                    inheritance_quality={
                        item["model"]: item["quality_checks_passed"]
                        for item in inheritance_payloads
                    },
                    uncertainty_notes=notes,
                )
            )
        total = connection.execute(
            (
                "SELECT count(*) FROM ranked_evidence WHERE run_id=? AND "
                "mode='filtering_phenotype_inheritance'"
            ),
            [run_id],
        ).fetchone()[0]
    report = ResearchReport(
        candidates=candidates,
        critic=critic,
        limitations=LIMITATIONS,
        release_citations=citations,
        total_ranked=total,
    )
    atomic_json(output, report.model_dump(mode="json"))
    return report
