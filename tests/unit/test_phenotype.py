from rare_disease_agent.synthetic.cases import load_synthetic_phenotype_store
from rare_disease_agent.tools.phenotype.normalize import normalize_hpo_terms
from rare_disease_agent.tools.phenotype.tools import PhenotypeToolbox


def test_hpo_normalization_reports_duplicates_invalid_unknown_and_obsolete() -> None:
    store = load_synthetic_phenotype_store()

    result = normalize_hpo_terms(
        ["HP:0001250", "hp:0001250", "HP:0009999", "HP:9999999", "seizure"],
        store.ontology,
    )

    assert [term.hpo_id for term in result.terms] == ["HP:0001250"]
    assert result.replacements == {"HP:0009999": "HP:0001250"}
    assert result.unknown_ids == ["HP:9999999"]
    assert result.invalid_ids == ["seizure"]
    assert any("Duplicate" in warning for warning in result.warnings)


def test_ontology_similarity_and_gene_scoring_are_deterministic() -> None:
    store = load_synthetic_phenotype_store()
    toolbox = PhenotypeToolbox(
        store,
        patient_hpo=["HP:0001250", "HP:0001263"],
        run_id="phenotype-test",
    )

    related = toolbox.scorer.term_similarity("HP:0001263", "HP:0001249")
    unrelated = toolbox.scorer.term_similarity("HP:0001263", "HP:0004322")
    causal = toolbox.get_gene_phenotype_score("SYN_CAUSAL")
    poor_fit = toolbox.get_gene_phenotype_score("SYN_PATH")

    assert related > unrelated
    assert causal.score > poor_fit.score
    assert causal.method == "resnik_bma"
    assert causal.data_version == "synthetic-hpo-2026-09-03"
    assert causal.provenance.run_id == "phenotype-test"
    assert causal.provenance.git_commit


def test_typed_phenotype_tools_include_provenance_and_rank_known_associations() -> None:
    store = load_synthetic_phenotype_store()
    toolbox = PhenotypeToolbox(
        store,
        patient_hpo=["HP:0001250", "HP:0001263"],
        run_id="phenotype-tools",
    )

    summary = toolbox.get_patient_hpo_summary()
    ranking = toolbox.rank_genes_by_phenotype(["SYN_PATH", "SYN_CAUSAL", "SYN_NOVEL"])
    gene = toolbox.get_gene_hpo_associations("SYN_CAUSAL")
    disease = toolbox.get_disease_hpo_associations("SYNTH:0001")

    assert summary.term_count == 2
    assert ranking[0].gene == "SYN_CAUSAL"
    assert any(item.gene == "SYN_NOVEL" and not item.association_known for item in ranking)
    assert len(gene.associations) == 2
    assert len(disease.associations) == 2
    assert gene.provenance.tool_version == "phenotype-tools-v1"


def test_missing_phenotype_is_explicit_and_scores_zero() -> None:
    store = load_synthetic_phenotype_store()
    toolbox = PhenotypeToolbox(store, patient_hpo=[], run_id="missing-phenotype")

    assert toolbox.get_patient_hpo_summary().term_count == 0
    assert toolbox.get_gene_phenotype_score("SYN_CAUSAL").score == 0
