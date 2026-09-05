from rare_disease_agent.config import PreliminaryScoringConfig
from rare_disease_agent.ranking.scoring import PreliminaryRanker


def rows():
    return [
        {
            "variant_id": "causal",
            "gene": "CAUSAL",
            "quality": 95,
            "allele_frequency": 0.00001,
            "consequence": "missense_variant",
        },
        {
            "variant_id": "pathogenic-decoy",
            "gene": "DECOY",
            "quality": 100,
            "allele_frequency": 0.00001,
            "consequence": "stop_gained",
        },
    ]


def test_ranking_features_are_separate_and_weighted_deterministically() -> None:
    ranker = PreliminaryRanker(
        weights=PreliminaryScoringConfig().model_dump(), run_id="ranking-test"
    )

    ranked = ranker.rank(
        rows(),
        phenotype_scores={"CAUSAL": 0.8, "DECOY": 0.0},
        inheritance_scores={"causal": 1.0, "pathogenic-decoy": 0.2},
    )

    assert ranked[0].variant_id == "causal"
    assert ranked[0].features.phenotype == 0.8
    assert ranked[0].features.inheritance == 1
    assert ranked[0].provenance.method == "weighted_linear_score"


def test_ablation_reports_rank_improvement_from_evidence() -> None:
    ranker = PreliminaryRanker(
        weights=PreliminaryScoringConfig().model_dump(), run_id="ablation-test"
    )

    ablations, _ = ranker.ablations(
        rows(),
        phenotype_scores={"CAUSAL": 0.8},
        inheritance_scores={"causal": 1.0},
        causal_variant_id="causal",
    )

    by_mode = {item.mode: item for item in ablations}
    assert by_mode["filtering_only"].causal_variant_rank == 2
    assert by_mode["filtering_phenotype_inheritance"].causal_variant_rank == 1
    assert by_mode["filtering_phenotype_inheritance"].top_1 is True
