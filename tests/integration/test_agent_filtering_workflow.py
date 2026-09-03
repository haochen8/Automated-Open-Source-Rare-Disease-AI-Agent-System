import json
from pathlib import Path

from rare_disease_agent.agents.schemas import AgentDecision
from rare_disease_agent.agents.variant_agent import VariantFilteringAgent
from rare_disease_agent.config import VariantFilteringAgentConfig
from rare_disease_agent.llm.errors import LLMUnavailableError
from rare_disease_agent.llm.mock import MockLLMBackend
from rare_disease_agent.tools.variants.agent_tools import VariantToolbox, VariantToolError
from rare_disease_agent.workflows.mock_strategy import default_mock_decisions
from rare_disease_agent.workflows.track1_filtering import VariantFilteringWorkflow


def make_decision(action: str, parameters: dict, rationale: str = "Safe test decision.") -> dict:
    return {
        "action": action,
        "rationale": rationale,
        "parameters": parameters,
        "expected_effect": "Exercise a bounded workflow transition.",
        "risk_of_false_negative": "low",
    }


def run_workflow(
    phase2_parquet: Path,
    tmp_path: Path,
    responses,
    *,
    config: VariantFilteringAgentConfig | None = None,
    toolbox: VariantToolbox | None = None,
):
    backend = MockLLMBackend(structured_responses=responses)
    workflow = VariantFilteringWorkflow(
        parquet_path=phase2_parquet,
        agent=VariantFilteringAgent(backend),
        run_directory=tmp_path / "run",
        config=config or VariantFilteringAgentConfig(),
        run_id="synthetic-test",
        causal_variant_id="SYNTH-CAUSAL-001",
        toolbox=toolbox,
    )
    return workflow.run()


def test_multi_branch_workflow_preserves_causal_variant_and_audits_every_decision(
    phase2_parquet: Path, tmp_path: Path
) -> None:
    result = run_workflow(
        phase2_parquet,
        tmp_path,
        default_mock_decisions(),
    )

    assert result.metrics.initial_candidates == 18
    assert result.metrics.final_candidates == 10
    assert result.metrics.causal_variant_preserved is True
    assert result.metrics.reduction_ratio == 10 / 18
    assert result.metrics.stop_reason == "candidate_set_sufficient"
    assert result.metrics.final_branch == "ensemble"
    assert result.state.complete is True
    assert len(result.state.filter_history) == 9
    branches = {summary.branch for summary in result.state.candidate_sets}
    assert {
        "conservative_rare",
        "pathogenicity_impact",
        "phenotype_priority",
        "ensemble",
    } <= branches

    lines = [json.loads(line) for line in result.audit_path.read_text().splitlines()]
    decisions = [line for line in lines if line["record_type"] == "agent_decision"]
    assert len(decisions) == 9
    assert all(record["prompt_version"] == "variant-filtering-v1" for record in decisions)
    assert all(len(record["prompt_hash"]) == 64 for record in decisions)
    assert result.metrics_path.is_file()
    assert result.candidates_path.is_file()


def test_stop_decision_terminates_without_tool_call(phase2_parquet: Path, tmp_path: Path) -> None:
    result = run_workflow(
        phase2_parquet,
        tmp_path,
        [make_decision("stop", {"branch": "all", "reason": "enough_evidence"})],
    )

    assert result.state.iteration == 1
    assert result.state.tool_calls == 0
    assert result.state.stop_reason == "enough_evidence"
    assert result.metrics.final_candidates == 18


def test_duplicate_decisions_are_rejected_then_terminate(
    phase2_parquet: Path, tmp_path: Path
) -> None:
    first = make_decision(
        "filter_frequency",
        {
            "source_branch": "all",
            "target_branch": "rare",
            "maximum_allele_frequency": 0.01,
        },
    )
    repeated = make_decision(
        "filter_frequency",
        {
            "source_branch": "all",
            "target_branch": "rare_again",
            "maximum_allele_frequency": 0.01,
        },
    )
    result = run_workflow(
        phase2_parquet,
        tmp_path,
        [first, repeated, repeated],
        config=VariantFilteringAgentConfig(max_repeated_decisions=2),
    )

    assert result.state.stop_reason == "repeated_decision_limit_reached"
    assert result.state.tool_calls == 1
    assert [record.outcome for record in result.state.filter_history] == [
        "executed",
        "rejected",
        "rejected",
    ]
    assert "already applied" in result.state.filter_history[-1].feedback


def test_max_iterations_is_enforced(phase2_parquet: Path, tmp_path: Path) -> None:
    responses = [
        make_decision("inspect_statistics", {"branch": "all"}),
        make_decision("count_variants", {"branch": "all"}),
        make_decision("sample_candidates", {"branch": "all", "limit": 2}),
    ]
    result = run_workflow(
        phase2_parquet,
        tmp_path,
        responses,
        config=VariantFilteringAgentConfig(max_iterations=2),
    )

    assert result.state.iteration == 2
    assert result.state.stop_reason == "maximum_iterations_reached"
    assert result.state.tool_calls == 2


def test_max_tool_calls_is_enforced(phase2_parquet: Path, tmp_path: Path) -> None:
    result = run_workflow(
        phase2_parquet,
        tmp_path,
        [
            make_decision("inspect_statistics", {"branch": "all"}),
            make_decision("count_variants", {"branch": "all"}),
        ],
        config=VariantFilteringAgentConfig(max_tool_calls=1),
    )

    assert result.state.stop_reason == "maximum_tool_calls_reached"
    assert result.state.tool_calls == 1


def test_malformed_llm_output_hits_invalid_limit(phase2_parquet: Path, tmp_path: Path) -> None:
    result = run_workflow(
        phase2_parquet,
        tmp_path,
        ["not-json", "still-not-json"],
        config=VariantFilteringAgentConfig(max_invalid_decisions=2),
    )

    assert result.state.stop_reason == "invalid_decision_limit_reached"
    assert result.state.invalid_decisions == 2
    assert result.state.tool_calls == 0


def test_llm_unavailable_stops_immediately(phase2_parquet: Path, tmp_path: Path) -> None:
    result = run_workflow(
        phase2_parquet,
        tmp_path,
        [LLMUnavailableError("synthetic outage")],
    )

    assert result.state.stop_reason == "llm_unavailable"
    assert result.state.iteration == 1
    assert result.metrics.causal_variant_preserved is True


def test_tool_errors_hit_retry_limit(phase2_parquet: Path, tmp_path: Path) -> None:
    class FailingToolbox(VariantToolbox):
        def execute(self, decision: AgentDecision):
            raise VariantToolError("synthetic tool failure")

    result = run_workflow(
        phase2_parquet,
        tmp_path,
        [
            make_decision("inspect_statistics", {"branch": "all"}),
            make_decision("count_variants", {"branch": "all"}),
        ],
        config=VariantFilteringAgentConfig(max_tool_errors=2),
        toolbox=FailingToolbox(phase2_parquet),
    )

    assert result.state.stop_reason == "tool_error_limit_reached"
    assert result.state.tool_errors == 2
    assert all(record.outcome == "error" for record in result.state.filter_history)


def test_minimum_candidate_floor_rejects_aggressive_branch(
    phase2_parquet: Path, tmp_path: Path
) -> None:
    result = run_workflow(
        phase2_parquet,
        tmp_path,
        [
            make_decision(
                "filter_frequency",
                {
                    "source_branch": "all",
                    "target_branch": "too_small",
                    "maximum_allele_frequency": 0.01,
                },
            ),
            make_decision("stop", {"branch": "all", "reason": "safety_floor_reached"}),
        ],
        config=VariantFilteringAgentConfig(minimum_candidate_count=15),
    )

    assert result.state.filter_history[0].outcome == "rejected"
    assert result.metrics.final_candidates == 18
    assert result.metrics.causal_variant_preserved is True
    assert "too_small" not in {summary.branch for summary in result.state.candidate_sets}


def test_no_reduction_limit_terminates(phase2_parquet: Path, tmp_path: Path) -> None:
    result = run_workflow(
        phase2_parquet,
        tmp_path,
        [
            make_decision(
                "filter_quality",
                {"source_branch": "all", "target_branch": "q0", "minimum_quality": 0},
            ),
            make_decision(
                "filter_quality",
                {"source_branch": "all", "target_branch": "q1", "minimum_quality": 1},
            ),
        ],
        config=VariantFilteringAgentConfig(max_no_reduction_attempts=2),
    )

    assert result.state.stop_reason == "no_meaningful_reduction_limit_reached"
    assert result.state.no_reduction_attempts == 2


def test_state_is_serializable(phase2_parquet: Path, tmp_path: Path) -> None:
    result = run_workflow(
        phase2_parquet,
        tmp_path,
        [make_decision("stop", {"branch": "all", "reason": "serialization_test"})],
    )

    reloaded = json.loads(result.state.model_dump_json())

    assert reloaded["run_id"] == "synthetic-test"
    assert reloaded["complete"] is True
