"""Inspectable ranking and benchmark result schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from rare_disease_agent.reporting.provenance import EvidenceProvenance, RunProvenance

RankingMode = Literal[
    "filtering_only",
    "filtering_phenotype",
    "filtering_inheritance",
    "filtering_phenotype_inheritance",
]


class VariantRankingFeatures(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quality: float = Field(ge=0, le=1)
    rarity: float = Field(ge=0, le=1)
    consequence: float = Field(ge=0, le=1)
    phenotype: float = Field(ge=0, le=1)
    inheritance: float = Field(ge=0, le=1)


class RankedVariant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    variant_id: str
    gene: str
    rank: int = Field(ge=1)
    features: VariantRankingFeatures
    raw_score: float = Field(ge=0, le=1)
    mode: RankingMode
    provenance: EvidenceProvenance


class AblationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: RankingMode
    causal_variant_rank: int | None = Field(default=None, ge=1)
    top_1: bool | None = None
    top_5: bool | None = None
    top_10: bool | None = None


class PhenotypeAblationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    removed_hpo_term: str | None
    full_phenotype_causal_rank: int | None = Field(default=None, ge=1)
    partial_phenotype_causal_rank: int | None = Field(default=None, ge=1)
    full_phenotype_score: float = Field(ge=0, le=1)
    partial_phenotype_score: float = Field(ge=0, le=1)
    phenotype_score_delta: float


class Track1Metrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    case: str
    initial_candidates: int = Field(ge=0)
    final_candidates: int = Field(ge=0)
    causal_variant_preserved: bool
    causal_variant_rank: int | None = Field(default=None, ge=1)
    top_1: bool
    top_5: bool
    top_10: bool
    novel_gene_rescue_preserved: bool
    expected_inheritance_model: str
    true_inheritance_model_identified: bool
    false_positive_inheritance_models: list[str]
    uncertain_inheritance_models: list[str]
    ablations: list[AblationResult]
    phenotype_ablation: PhenotypeAblationResult
    process_rss_mb_start: float = Field(ge=0)
    process_rss_mb_end: float = Field(ge=0)
    provenance: RunProvenance


class SyntheticBenchmarkResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cases: list[Track1Metrics]
    all_causal_variants_preserved: bool
    top_5_rate: float = Field(ge=0, le=1)
    inheritance_model_accuracy: float = Field(ge=0, le=1)
    peak_process_rss_mb: float = Field(ge=0)
