"""Transparent deterministic feature calculation and weighted ranking."""

from __future__ import annotations

import math
from typing import Any

from rare_disease_agent.ranking.schemas import (
    AblationResult,
    RankedVariant,
    RankingMode,
    VariantRankingFeatures,
)
from rare_disease_agent.reporting.provenance import SoftwareIdentity, evidence_provenance

TOOL_VERSION = "preliminary-ranking-v1"

CONSEQUENCE_SCORES = {
    "transcript_ablation": 1.0,
    "stop_gained": 1.0,
    "frameshift_variant": 1.0,
    "splice_acceptor_variant": 0.9,
    "splice_donor_variant": 0.9,
    "start_lost": 0.85,
    "stop_lost": 0.85,
    "missense_variant": 0.7,
    "inframe_insertion": 0.6,
    "inframe_deletion": 0.6,
    "synonymous_variant": 0.15,
    "intron_variant": 0.1,
    "intergenic_variant": 0.05,
}

MODE_FEATURES: dict[RankingMode, tuple[str, ...]] = {
    "filtering_only": ("quality", "rarity", "consequence"),
    "filtering_phenotype": ("quality", "rarity", "consequence", "phenotype"),
    "filtering_inheritance": ("quality", "rarity", "consequence", "inheritance"),
    "filtering_phenotype_inheritance": (
        "quality",
        "rarity",
        "consequence",
        "phenotype",
        "inheritance",
    ),
}


class PreliminaryRanker:
    def __init__(
        self,
        *,
        weights: dict[str, float],
        run_id: str,
        software: SoftwareIdentity | None = None,
    ) -> None:
        required = {"quality", "rarity", "consequence", "phenotype", "inheritance"}
        if set(weights) != required or any(
            not math.isfinite(value) or value < 0 for value in weights.values()
        ):
            raise ValueError(f"Ranking weights must be non-negative and define {sorted(required)}")
        if sum(weights.values()) <= 0:
            raise ValueError("At least one ranking weight must be positive")
        self.weights = weights
        self.run_id = run_id
        self.software = software

    @staticmethod
    def features_for_row(
        row: dict[str, Any],
        *,
        phenotype_scores: dict[str, float],
        inheritance_scores: dict[str, float],
    ) -> VariantRankingFeatures:
        quality_raw = row.get("quality")
        quality = 0.5 if quality_raw is None else min(max(float(quality_raw) / 100, 0), 1)
        frequency = row.get("allele_frequency")
        rarity = 0.6 if frequency is None else max(0.0, 1 - min(float(frequency) / 0.01, 1))
        consequence = CONSEQUENCE_SCORES.get(str(row.get("consequence") or ""), 0.1)
        return VariantRankingFeatures(
            quality=round(quality, 6),
            rarity=round(rarity, 6),
            consequence=consequence,
            phenotype=round(phenotype_scores.get(str(row.get("gene") or "").upper(), 0), 6),
            inheritance=round(inheritance_scores.get(str(row["variant_id"]), 0), 6),
        )

    def rank(
        self,
        rows: list[dict[str, Any]],
        *,
        phenotype_scores: dict[str, float],
        inheritance_scores: dict[str, float],
        mode: RankingMode = "filtering_phenotype_inheritance",
    ) -> list[RankedVariant]:
        included = MODE_FEATURES[mode]
        weight_total = sum(self.weights[name] for name in included)
        if weight_total <= 0:
            raise ValueError(f"Weights for {mode} sum to zero")
        prepared: list[tuple[dict[str, Any], VariantRankingFeatures, float]] = []
        for row in rows:
            features = self.features_for_row(
                row,
                phenotype_scores=phenotype_scores,
                inheritance_scores=inheritance_scores,
            )
            score = (
                sum(getattr(features, name) * self.weights[name] for name in included)
                / weight_total
            )
            prepared.append((row, features, round(score, 6)))
        prepared.sort(key=lambda item: (-item[2], str(item[0]["variant_id"])))
        provenance = evidence_provenance(
            run_id=self.run_id,
            tool_version=TOOL_VERSION,
            data_version="synthetic-annotations-v1",
            method="weighted_linear_score",
            parameters={"weights": self.weights, "included_features": list(included)},
            software=self.software,
        )
        return [
            RankedVariant(
                variant_id=str(row["variant_id"]),
                gene=str(row.get("gene") or ""),
                rank=index,
                features=features,
                raw_score=score,
                mode=mode,
                provenance=provenance,
            )
            for index, (row, features, score) in enumerate(prepared, start=1)
        ]

    def ablations(
        self,
        rows: list[dict[str, Any]],
        *,
        phenotype_scores: dict[str, float],
        inheritance_scores: dict[str, float],
        causal_variant_id: str,
    ) -> tuple[list[AblationResult], dict[RankingMode, list[RankedVariant]]]:
        results: list[AblationResult] = []
        rankings: dict[RankingMode, list[RankedVariant]] = {}
        for mode in MODE_FEATURES:
            ranked = self.rank(
                rows,
                phenotype_scores=phenotype_scores,
                inheritance_scores=inheritance_scores,
                mode=mode,
            )
            rankings[mode] = ranked
            causal = next((item for item in ranked if item.variant_id == causal_variant_id), None)
            rank = causal.rank if causal else None
            results.append(
                AblationResult(
                    mode=mode,
                    causal_variant_rank=rank,
                    top_1=rank <= 1 if rank else False,
                    top_5=rank <= 5 if rank else False,
                    top_10=rank <= 10 if rank else False,
                )
            )
        return results, rankings
