import pytest
from pydantic import ValidationError

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
