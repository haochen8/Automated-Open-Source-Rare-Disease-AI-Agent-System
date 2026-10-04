import pytest
from pydantic import ValidationError

from rare_disease_agent.reporting.provenance import SoftwareIdentity
from rare_disease_agent.synthetic.cases import synthetic_pedigree
from rare_disease_agent.tools.inheritance.evaluator import InheritanceEvaluator
from rare_disease_agent.tools.inheritance.schemas import (
    GenotypeCall,
    Individual,
    Pedigree,
    VariantGenotypes,
)


def variant(
    *,
    variant_id: str = "v1",
    gene: str = "GENE",
    chromosome: str = "1",
    proband: str | None = "0/1",
    mother: str | None = "0/0",
    father: str | None = "0/0",
    proband_quality: float = 50,
) -> VariantGenotypes:
    return VariantGenotypes(
        variant_id=variant_id,
        gene=gene,
        chromosome=chromosome,
        calls=[
            GenotypeCall(individual_id="PROBAND", genotype=proband, quality=proband_quality),
            GenotypeCall(individual_id="MOTHER", genotype=mother, quality=50),
            GenotypeCall(individual_id="FATHER", genotype=father, quality=50),
        ],
    )


def test_pedigree_validation_is_extensible_and_rejects_invalid_relations() -> None:
    pedigree = Pedigree(
        proband_id="CHILD",
        individuals=[
            Individual(id="CHILD", affected=True),
            Individual(id="SIBLING", affected=False),
            Individual(id="GRANDPARENT", affected=None),
        ],
    )
    assert len(pedigree.individuals) == 3

    with pytest.raises(ValidationError, match="proband_id"):
        Pedigree(proband_id="MISSING", individuals=[Individual(id="CHILD")])


def test_de_novo_requires_parental_evidence_and_handles_missing_parent() -> None:
    evaluator = InheritanceEvaluator(synthetic_pedigree(), run_id="inheritance-test")

    positive = evaluator.evaluate_de_novo(variant())
    missing = evaluator.evaluate_de_novo(variant(father=None))

    assert positive.fit == 1
    assert positive.quality_checks_passed is True
    assert missing.fit < 0.8
    assert "unconfirmed" in missing.warnings[0]


def test_dominant_recessive_and_x_linked_evaluators() -> None:
    dominant_pedigree = Pedigree(
        proband_id="PROBAND",
        mother_id="MOTHER",
        father_id="FATHER",
        individuals=[
            Individual(id="PROBAND", sex="male", affected=True),
            Individual(id="MOTHER", sex="female", affected=False),
            Individual(id="FATHER", sex="male", affected=True),
        ],
    )
    dominant = InheritanceEvaluator(dominant_pedigree, run_id="dominant").evaluate_dominant(
        variant(father="0/1")
    )
    evaluator = InheritanceEvaluator(synthetic_pedigree(), run_id="models")
    recessive = evaluator.evaluate_homozygous_recessive(
        variant(proband="1/1", mother="0/1", father="0/1")
    )
    x_linked = evaluator.evaluate_x_linked(
        variant(chromosome="X", proband="1", mother="0/1", father="0")
    )

    assert dominant.fit == 1
    assert recessive.fit == 1
    assert x_linked.fit == 1
    assert all(item.provenance.run_id for item in (dominant, recessive, x_linked))


def test_low_quality_genotype_caps_inheritance_fit() -> None:
    evaluator = InheritanceEvaluator(synthetic_pedigree(), run_id="low-quality")

    result = evaluator.evaluate_de_novo(variant(proband_quality=8))

    assert result.fit == 0.5
    assert result.quality_checks_passed is False


@pytest.mark.parametrize("mother,father", [(None, None), ("0/0", None), (None, "0/0")])
def test_sparse_dominant_evidence_cannot_be_a_strong_match(mother, father):
    evaluator = InheritanceEvaluator(synthetic_pedigree(), run_id="sparse-dominance")
    result = evaluator.evaluate([variant(mother=mother, father=father)])
    dominant = next(item for item in result.evidence if item.model == "autosomal_dominant")
    summary = next(item for item in result.summaries if item.model == "autosomal_dominant")

    assert 0 < dominant.fit < 0.8
    assert summary.strong_matches == 0
    assert any("insufficient" in warning.lower() for warning in dominant.warnings)


def test_affected_carrier_relative_supports_dominance_without_complete_parents():
    pedigree = synthetic_pedigree()
    pedigree.individuals[1].affected = True
    evaluator = InheritanceEvaluator(pedigree, run_id="familial-dominance")

    assert evaluator.evaluate_dominant(variant(mother="0/1", father=None)).fit == 1
    # An unaffected reference relative alone supplies no positive segregation evidence.
    pedigree.individuals[1].affected = False
    assert evaluator.evaluate_dominant(variant(mother="0/0", father=None)).fit < 0.8


def test_complete_reference_parents_retain_de_novo_dominant_compatibility():
    evaluator = InheritanceEvaluator(synthetic_pedigree(), run_id="complete-trio")
    assert evaluator.evaluate_dominant(variant()).fit == 1


def test_compound_heterozygous_phase_is_confirmed_only_with_parental_origin() -> None:
    evaluator = InheritanceEvaluator(synthetic_pedigree(), run_id="compound")
    confirmed = evaluator.find_compound_heterozygous_pairs(
        [
            variant(variant_id="a", mother="0/1", father="0/0"),
            variant(variant_id="b", mother="0/0", father="0/1"),
        ]
    )[0]
    unknown = evaluator.find_compound_heterozygous_pairs(
        [
            variant(variant_id="c", mother=None, father=None),
            variant(variant_id="d", mother=None, father=None),
        ]
    )[0]

    assert confirmed.phase == "confirmed_trans"
    assert confirmed.confidence == 0.95
    assert unknown.phase == "unknown"
    assert unknown.confidence < confirmed.confidence


def test_low_quality_compound_het_remains_possible_not_confirmed() -> None:
    evaluator = InheritanceEvaluator(synthetic_pedigree(), run_id="compound-low-quality")
    result = evaluator.find_compound_heterozygous_pairs(
        [
            variant(variant_id="a", mother="0/1", father="0/0", proband_quality=8),
            variant(variant_id="b", mother="0/0", father="0/1"),
        ]
    )[0]

    assert result.phase == "possible_trans"
    assert result.confidence == 0.5
    assert "low genotype quality" in result.warnings[0]


def test_autosomal_recessive_summary_includes_compound_het_evidence() -> None:
    evaluator = InheritanceEvaluator(synthetic_pedigree(), run_id="recessive-summary")
    result = evaluator.evaluate(
        [
            variant(variant_id="a", mother="0/1", father="0/0"),
            variant(variant_id="b", mother="0/0", father="0/1"),
        ]
    )

    recessive = [item for item in result.evidence if item.model == "autosomal_recessive"]
    assert all(item.fit == 0.95 for item in recessive)


def test_same_parent_origin_does_not_support_confirmed_trans():
    evaluator = InheritanceEvaluator(synthetic_pedigree(), run_id="same-origin")
    pair = evaluator.find_compound_heterozygous_pairs(
        [
            variant(variant_id="first", mother="0/1", father="0/0"),
            variant(variant_id="second", mother="0/1", father="0/0"),
        ]
    )[0]
    assert pair.phase == "unknown"
    assert pair.confidence < 0.4


def test_deep_pedigree_validation_is_iterative_and_rejects_cycles():
    people = [
        Individual(id=f"person{i}", mother_id=f"person{i + 1}" if i < 999 else None)
        for i in range(1000)
    ]
    assert len(Pedigree(proband_id="person0", individuals=people).individuals) == 1000
    people[-1].mother_id = "person0"
    with pytest.raises(ValueError, match="cycle"):
        Pedigree(proband_id="person0", individuals=people)


@pytest.mark.parametrize("gene,chromosome", [("UNKNOWN", "1"), ("UNASSIGNED", "1"), ("GENE", "2")])
def test_unknown_gene_or_different_chromosome_cannot_define_compound_pair(gene, chromosome):
    evaluator = InheritanceEvaluator(synthetic_pedigree(), run_id="synthetic-pair-context")
    first = variant(variant_id="first", gene=gene)
    second = variant(variant_id="second", gene=gene, chromosome=chromosome)
    assert evaluator.find_compound_heterozygous_pairs([first, second]) == []
    assert evaluator.evaluate([first]).evidence


PAIR_SOFTWARE = SoftwareIdentity(
    git_commit="synthetic-pair-oracle", git_dirty=False, python_version="synthetic", dependencies={}
)


@pytest.mark.parametrize("affected", [True, None, False])
@pytest.mark.parametrize(
    "first_values,second_values",
    [
        ({"mother": "0/1"}, {"father": "0/1"}),
        ({"mother": "0/1"}, {"mother": "0/1"}),
        ({"mother": "0/1"}, {"mother": None, "father": None}),
        ({"mother": None, "father": None}, {"mother": None, "father": None}),
        ({"proband": "0|1"}, {"proband": "1|0"}),
        ({"mother": "0/1", "proband_quality": 8}, {"father": "0/1"}),
        ({"proband": "1/1", "mother": "0/1", "father": "0/1"}, {}),
        ({"proband": None}, {}),
        ({"proband": "0/."}, {}),
        ({"proband": "0/0"}, {}),
        ({"proband": "1/2"}, {}),
        ({"gene": "UNKNOWN"}, {"gene": "UNKNOWN"}),
        ({}, {"chromosome": "2"}),
        ({"gene": " gene "}, {"chromosome": "chr1"}),
    ],
)
def test_pair_entry_point_matches_complete_filtered_general_evaluation(
    affected, first_values, second_values
):
    pedigree = synthetic_pedigree()
    pedigree.individual("PROBAND").affected = affected
    evaluator = InheritanceEvaluator(pedigree, run_id="synthetic-pair", software=PAIR_SOFTWARE)
    first = variant(variant_id="z-first", **first_values)
    second = variant(variant_id="a-second", **second_values)
    before = [v.model_dump() for v in [first, second]]
    general = evaluator.evaluate([first, second])
    pair = evaluator.evaluate_pair(first, second)
    assert pair.compound_heterozygous_pairs == general.compound_heterozygous_pairs
    assert pair.evidence == [
        e for e in general.evidence if e.model in {"compound_heterozygous", "autosomal_recessive"}
    ]
    assert [v.model_dump() for v in [first, second]] == before
    assert [e.variant_id for e in pair.evidence if e.model == "autosomal_recessive"] == [
        "z-first",
        "a-second",
    ]
    if first_values.get("proband") == "1/1":
        assert pair.compound_heterozygous_pairs == []
        assert pair.evidence[0].fit == (0.5 if affected is None else 1.0)
        assert "No recessive genotype pattern identified." not in pair.evidence[0].warnings


@pytest.mark.parametrize("case", ["missing_calls", "low_parent_quality", "low_fraction"])
def test_pair_entry_point_preserves_missing_calls_and_endpoint_quality(case):
    evaluator = InheritanceEvaluator(
        synthetic_pedigree(), run_id="synthetic-quality", software=PAIR_SOFTWARE
    )
    first = variant(variant_id="first", mother="0/1")
    second = variant(variant_id="second", father="0/1")
    if case == "missing_calls":
        first.calls = []
    elif case == "low_parent_quality":
        first.calls[1].quality = 8
    else:
        first.calls[0].alternate_fraction = 0.1
    full = evaluator.evaluate([first, second])
    pair = evaluator.evaluate_pair(first, second)
    assert pair.compound_heterozygous_pairs == full.compound_heterozygous_pairs
    assert pair.evidence == [
        e for e in full.evidence if e.model in {"compound_heterozygous", "autosomal_recessive"}
    ]


@pytest.mark.parametrize("case", ["formed_pair", "homozygous", "missing_calls"])
def test_pair_skips_homozygous_baselines_only_when_decoded_pair_forms(case, monkeypatch):
    evaluator = InheritanceEvaluator(
        synthetic_pedigree(), run_id="synthetic-baseline-calls", software=PAIR_SOFTWARE
    )
    first = variant(variant_id="z-first", mother="0/1")
    second = variant(variant_id="a-second", father="0/1")
    if case == "homozygous":
        first.calls[0].genotype = "1/1"
        first.calls[2].genotype = "0/1"
    elif case == "missing_calls":
        first.calls = []
    full = evaluator.evaluate([first, second])
    evaluated = []
    original = evaluator.evaluate_homozygous_recessive

    def record(variant):
        evaluated.append(variant.variant_id)
        return original(variant)

    monkeypatch.setattr(evaluator, "evaluate_homozygous_recessive", record)
    pair = evaluator.evaluate_pair(first, second)
    assert evaluated == ([] if case == "formed_pair" else ["z-first", "a-second"])
    assert bool(pair.compound_heterozygous_pairs) == (case == "formed_pair")
    assert pair.compound_heterozygous_pairs == full.compound_heterozygous_pairs
    assert pair.evidence == [
        e for e in full.evidence if e.model in {"compound_heterozygous", "autosomal_recessive"}
    ]


def test_pair_endpoint_payloads_have_independent_expected_explanations_and_provenance():
    evaluator = InheritanceEvaluator(
        synthetic_pedigree(), run_id="synthetic-payload", software=PAIR_SOFTWARE
    )
    first = variant(variant_id="first", mother="0/1", proband_quality=8)
    second = variant(variant_id="second", mother=None, father=None)
    pair_warning = "Only one parental origin is informative; trans is possible."
    quality_warning = "Low genotype quality for: PROBAND."

    def provenance(method):
        return {
            "tool_version": "inheritance-evaluator-v2",
            "data_version": "pedigree-input-v1",
            "method": method,
            "parameters": {"minimum_genotype_quality": 20},
            "git_commit": "synthetic-pair-oracle",
            "git_dirty": False,
            "run_id": "synthetic-payload",
        }

    expected = []
    for model in ["compound_heterozygous", "autosomal_recessive"]:
        for ident in ["first", "second"]:
            compound = model == "compound_heterozygous"
            expected.append(
                {
                    "variant_id": ident,
                    "gene": "GENE",
                    "model": model,
                    "fit": 0.5 if ident == "first" else 0.65,
                    "proband_genotype": "0/1",
                    "mother_genotype": "0/1" if ident == "first" else None,
                    "father_genotype": "0/0" if ident == "first" else None,
                    "quality_checks_passed": ident != "first",
                    "evidence": []
                    if compound
                    else ["Best homozygous or compound-heterozygous recessive evidence."],
                    "warnings": ([pair_warning] if compound else [])
                    + ([quality_warning] if ident == "first" else []),
                    "provenance": provenance(model),
                }
            )
    result = evaluator.evaluate_pair(first, second).model_dump()
    assert result == {
        "evidence": expected,
        "compound_heterozygous_pairs": [
            {
                "gene": "GENE",
                "variant_a": "first",
                "variant_b": "second",
                "phase": "possible_trans",
                "confidence": 0.65,
                "evidence": [],
                "warnings": [pair_warning],
                "provenance": provenance("compound_heterozygous"),
            }
        ],
    }
