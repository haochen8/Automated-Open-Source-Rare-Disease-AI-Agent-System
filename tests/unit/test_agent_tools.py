from rare_disease_agent.agents.schemas import (
    BranchParameters,
    CreateRescueBranchParameters,
    FilterClinvarParameters,
    FilterConsequenceParameters,
    FilterFrequencyParameters,
    FilterQualityParameters,
    MergeBranchesParameters,
    SampleCandidatesParameters,
)
from rare_disease_agent.tools.variants.agent_tools import VariantToolbox


def test_statistics_and_samples_are_compact(phase2_parquet) -> None:
    toolbox = VariantToolbox(phase2_parquet)

    statistics = toolbox.get_variant_statistics(BranchParameters(branch="all"))
    sample = toolbox.sample_candidate_summary(SampleCandidatesParameters(branch="all", limit=2))

    assert statistics.data["count"] == 18
    assert statistics.data["population_frequency"]["missing"] == 1
    assert len(sample.data["samples"]) == 2
    assert "position" not in sample.data["samples"][0]
    assert "genotype" not in sample.data["samples"][0]


def test_reversible_filters_and_union_preserve_sources(phase2_parquet) -> None:
    toolbox = VariantToolbox(phase2_parquet)
    toolbox.create_rescue_branch(
        CreateRescueBranchParameters(source_branch="all", target_branch="conservative")
    )
    rare = toolbox.filter_by_population_frequency(
        FilterFrequencyParameters(
            source_branch="conservative",
            target_branch="rare",
            maximum_allele_frequency=0.01,
        )
    )
    damaging = toolbox.filter_by_consequence(
        FilterConsequenceParameters(
            source_branch="all",
            target_branch="damaging",
            allowed_consequences=["stop_gained", "frameshift_variant"],
        )
    )
    union = toolbox.combine_candidate_sets(
        MergeBranchesParameters(branches=["rare", "damaging"], target_branch="ensemble")
    )

    assert rare.after_count == 9
    assert damaging.after_count == 2
    assert union.after_count == 9
    assert len(toolbox.candidate_ids("all")) == 18
    assert "SYNTH-CAUSAL-001" in toolbox.candidate_ids("ensemble")


def test_frequency_filter_preserves_missing_and_pathogenic_rescue(phase2_parquet) -> None:
    toolbox = VariantToolbox(phase2_parquet)

    toolbox.filter_by_population_frequency(
        FilterFrequencyParameters(
            source_branch="all",
            target_branch="rare",
            maximum_allele_frequency=0.01,
        )
    )
    identifiers = toolbox.candidate_ids("rare")

    assert "SYN-NOVEL-MISSING-AF" in identifiers
    assert "SYN-COMMON-CLINVAR-RESCUE" in identifiers


def test_quality_and_clinvar_tools_use_validated_inputs(phase2_parquet) -> None:
    toolbox = VariantToolbox(phase2_parquet)

    quality = toolbox.filter_by_quality(
        FilterQualityParameters(source_branch="all", target_branch="quality", minimum_quality=20)
    )
    clinvar = toolbox.filter_by_clinvar(
        FilterClinvarParameters(
            source_branch="all",
            target_branch="clinvar",
            classifications=["Pathogenic", "Likely_pathogenic"],
        )
    )

    assert quality.after_count == 17
    assert clinvar.after_count == 2
    assert "SYNTH-CAUSAL-001" in toolbox.candidate_ids("clinvar")
