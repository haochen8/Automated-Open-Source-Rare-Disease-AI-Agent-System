import json
from pathlib import Path

import pytest

from rare_disease_agent.config import load_settings
from rare_disease_agent.llm.mock import MockLLMBackend
from rare_disease_agent.synthetic import SYNTHETIC_CASE_NAMES
from rare_disease_agent.workflows.mock_strategy import phase3_mock_decisions
from rare_disease_agent.workflows.track1_evidence import Track1EvidenceWorkflow


def run_case(case: str, tmp_path: Path):
    return Track1EvidenceWorkflow(
        case_name=case,
        backend=MockLLMBackend(structured_responses=phase3_mock_decisions()),
        settings=load_settings(),
        run_directory=tmp_path / case,
        run_id=f"test-{case}",
    ).run()


@pytest.mark.parametrize("case", SYNTHETIC_CASE_NAMES)
def test_all_synthetic_cases_preserve_and_rank_causal_variant(case: str, tmp_path: Path) -> None:
    result = run_case(case, tmp_path)

    assert result.metrics.causal_variant_preserved is True
    assert result.metrics.top_5 is True
    assert result.metrics.novel_gene_rescue_preserved is True
    assert result.metrics.true_inheritance_model_identified is True
    assert result.metrics.causal_variant_rank is not None
    assert result.filtering.state.causal_variant_rank == result.metrics.causal_variant_rank
    assert result.filtering.metrics.causal_variant_rank == result.metrics.causal_variant_rank
    assert result.metrics.process_rss_mb_end < 512
    assert result.metrics.provenance.hpo_data_version == "synthetic-hpo-2026-09-03"
    assert result.metrics_path.is_file()
    assert result.ranking_path.is_file()
    assert (tmp_path / case / "candidate_membership.duckdb").is_file()
    branches = {summary.branch for summary in result.filtering.state.candidate_sets}
    assert {
        "conservative_rare",
        "phenotype-priority",
        "inheritance-priority",
        "pathogenicity-priority",
        "novel-gene-rescue",
        "ensemble",
    } <= branches


def test_combined_evidence_improves_de_novo_causal_rank(tmp_path: Path) -> None:
    result = run_case("de-novo", tmp_path)
    ablations = {item.mode: item for item in result.metrics.ablations}

    assert ablations["filtering_only"].causal_variant_rank == 4
    assert ablations["filtering_phenotype_inheritance"].causal_variant_rank == 1
    assert result.metrics.phenotype_ablation.phenotype_score_delta > 0


def test_expected_inheritance_models_and_compound_phase_are_recorded(tmp_path: Path) -> None:
    for case in ("de-novo", "recessive", "compound-het", "x-linked"):
        result = run_case(case, tmp_path)
        evidence = json.loads(result.evidence_path.read_text(encoding="utf-8"))["inheritance"]
        matching = [
            item
            for item in evidence["evidence"]
            if item["variant_id"] == result.case.causal_variant_id
            and item["model"] == result.case.expected_model
        ]
        assert matching and matching[0]["fit"] >= 0.8
        assert matching[0]["provenance"]["run_id"] == result.metrics.run_id
        if case == "compound-het":
            pairs = evidence["compound_heterozygous_pairs"]
            assert any(
                item["gene"] == "SYN_CAUSAL" and item["phase"] == "confirmed_trans"
                for item in pairs
            )


def test_novel_gene_causal_variant_survives_rescue_without_hpo_association(
    tmp_path: Path,
) -> None:
    result = run_case("novel-gene", tmp_path)
    ranking = json.loads(result.ranking_path.read_text(encoding="utf-8"))
    causal = next(item for item in ranking if item["variant_id"] == "SYN-NOVEL-001")
    evidence = json.loads(result.evidence_path.read_text(encoding="utf-8"))

    assert causal["features"]["phenotype"] == 0
    assert causal["rank"] <= 5
    assert result.metrics.novel_gene_rescue_preserved is True
    novel = next(item for item in evidence["phenotype"] if item["gene"] == "SYN_NOVEL")
    assert novel["association_count"] == 0


def test_audit_contains_prompt_and_runtime_provenance(tmp_path: Path) -> None:
    result = run_case("de-novo", tmp_path)
    records = [
        json.loads(line)
        for line in result.filtering.audit_path.read_text(encoding="utf-8").splitlines()
    ]
    decisions = [item for item in records if item["record_type"] == "agent_decision"]
    provenance = [
        item
        for item in records
        if item.get("record_type") == "workflow_event" and item.get("event") == "run_provenance"
    ]

    assert all(item["prompt_version"] == "variant-filtering-v2" for item in decisions)
    assert any(item["observation_data"].get("provenance") for item in decisions)
    assert provenance[0]["data"]["software"]["git_commit"]
    assert "dependencies" in provenance[0]["data"]["software"]
