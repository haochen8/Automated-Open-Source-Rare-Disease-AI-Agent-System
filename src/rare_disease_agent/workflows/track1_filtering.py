"""Bounded LangGraph workflow for autonomous deterministic variant filtering."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import psutil
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict

from rare_disease_agent.agents.schemas import (
    AgentDecision,
    BranchParameters,
    FilterDecisionRecord,
    FilteringMetrics,
    ToolObservation,
    VariantFilteringState,
)
from rare_disease_agent.agents.variant_agent import VariantFilteringAgent
from rare_disease_agent.config import VariantFilteringAgentConfig
from rare_disease_agent.llm.errors import LLMError, LLMUnavailableError, StructuredOutputError
from rare_disease_agent.reporting.audit import AuditWriter
from rare_disease_agent.tools.variants.agent_tools import VariantToolbox, VariantToolError

REDUCTION_ACTIONS = {
    "filter_quality",
    "filter_frequency",
    "filter_consequence",
    "filter_gene",
    "filter_clinvar",
}


class FilteringRunResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    state: VariantFilteringState
    metrics: FilteringMetrics
    final_candidate_ids: list[str]
    audit_path: Path
    metrics_path: Path
    candidates_path: Path


def _validated_state(raw: VariantFilteringState | dict[str, Any]) -> VariantFilteringState:
    if isinstance(raw, VariantFilteringState):
        return raw.model_copy(deep=True)
    return VariantFilteringState.model_validate(raw)


class VariantFilteringWorkflow:
    def __init__(
        self,
        *,
        parquet_path: Path | str,
        agent: VariantFilteringAgent,
        run_directory: Path | str,
        config: VariantFilteringAgentConfig,
        run_id: str,
        causal_variant_id: str | None = None,
        toolbox: VariantToolbox | None = None,
    ) -> None:
        self.toolbox = toolbox or VariantToolbox(parquet_path)
        self.agent = agent
        self.config = config
        self.run_id = run_id
        self.causal_variant_id = causal_variant_id
        self.audit = AuditWriter(run_directory)
        self.graph = self._build_graph()

    def _initial_inspection(self, raw: VariantFilteringState | dict[str, Any]) -> dict[str, Any]:
        state = _validated_state(raw)
        inspected = self.toolbox.get_variant_statistics(BranchParameters(branch="all"))
        state.last_observation = ToolObservation(
            action="initial_inspection",
            branch="all",
            before_count=inspected.before_count,
            after_count=inspected.after_count,
            feedback=inspected.feedback,
            data=inspected.data,
        )
        state.candidate_sets = self.toolbox.candidate_summaries()
        self.audit.write_event(
            "initial_inspection",
            run_id=state.run_id,
            data={"count": inspected.after_count, "statistics": inspected.data},
        )
        return state.model_dump(mode="python")

    def _agent_decision(self, raw: VariantFilteringState | dict[str, Any]) -> dict[str, Any]:
        state = _validated_state(raw)
        if state.iteration >= state.max_iterations:
            state.complete = True
            state.stop_reason = "maximum_iterations_reached"
            return state.model_dump(mode="python")
        if state.tool_calls >= state.max_tool_calls:
            state.complete = True
            state.stop_reason = "maximum_tool_calls_reached"
            return state.model_dump(mode="python")

        state.iteration += 1
        try:
            state.pending_decision = self.agent.decide(state)
        except LLMUnavailableError as exc:
            state.invalid_decisions += 1
            state.last_observation = ToolObservation(
                action="invalid_decision",
                branch=state.current_branch,
                before_count=state.current_variant_count,
                after_count=state.current_variant_count,
                feedback=f"LLM unavailable: {type(exc).__name__}.",
            )
            state.complete = True
            state.stop_reason = "llm_unavailable"
            self.audit.write_event(
                "llm_unavailable",
                run_id=state.run_id,
                data={"iteration": state.iteration, "error_type": type(exc).__name__},
            )
        except (StructuredOutputError, LLMError, ValueError) as exc:
            state.invalid_decisions += 1
            state.last_observation = ToolObservation(
                action="invalid_decision",
                branch=state.current_branch,
                before_count=state.current_variant_count,
                after_count=state.current_variant_count,
                feedback=f"Invalid structured decision rejected: {type(exc).__name__}.",
            )
            if state.invalid_decisions >= self.config.max_invalid_decisions:
                state.complete = True
                state.stop_reason = "invalid_decision_limit_reached"
            self.audit.write_event(
                "invalid_decision",
                run_id=state.run_id,
                data={
                    "iteration": state.iteration,
                    "error_type": type(exc).__name__,
                    "invalid_decisions": state.invalid_decisions,
                },
            )
        return state.model_dump(mode="python")

    def _record(
        self,
        state: VariantFilteringState,
        decision: AgentDecision,
        observation: ToolObservation,
        outcome: Literal["executed", "rejected", "error", "stopped"],
    ) -> None:
        record = FilterDecisionRecord(
            timestamp=datetime.now(UTC),
            run_id=state.run_id,
            iteration=state.iteration,
            action=decision.action,
            parameters=decision.parameters,
            rationale=decision.rationale,
            expected_effect=decision.expected_effect,
            risk_of_false_negative=decision.risk_of_false_negative,
            before_count=observation.before_count,
            after_count=observation.after_count,
            branch=observation.branch,
            model=self.agent.backend.model_name,
            backend=self.agent.backend.backend_name,
            prompt_version=self.agent.prompt_version,
            prompt_hash=self.agent.prompt_hash,
            outcome=outcome,
            feedback=observation.feedback,
        )
        state.filter_history.append(record)
        self.audit.write_decision(record)

    def _rejected_observation(
        self, state: VariantFilteringState, decision: AgentDecision, feedback: str
    ) -> ToolObservation:
        parameters = decision.parameters
        branch = str(
            parameters.get("source_branch") or parameters.get("branch") or state.current_branch
        )
        return ToolObservation(
            action=decision.action,
            branch=branch,
            before_count=state.current_variant_count,
            after_count=state.current_variant_count,
            feedback=feedback,
        )

    def _execute_tool(self, raw: VariantFilteringState | dict[str, Any]) -> dict[str, Any]:
        state = _validated_state(raw)
        decision = state.pending_decision
        state.pending_decision = None
        if decision is None or state.complete:
            return state.model_dump(mode="python")

        signature = decision.signature()
        if signature in state.decision_signatures:
            state.repeated_decisions += 1
            observation = self._rejected_observation(
                state,
                decision,
                "This filter/action was already applied. Choose another strategy or stop.",
            )
            state.last_observation = observation
            self._record(state, decision, observation, "rejected")
            if state.repeated_decisions >= self.config.max_repeated_decisions:
                state.complete = True
                state.stop_reason = "repeated_decision_limit_reached"
            return state.model_dump(mode="python")
        state.decision_signatures.append(signature)

        if decision.action != "stop" and state.tool_calls >= state.max_tool_calls:
            observation = self._rejected_observation(
                state, decision, "Tool-call budget exhausted; action was not executed."
            )
            state.last_observation = observation
            state.complete = True
            state.stop_reason = "maximum_tool_calls_reached"
            self._record(state, decision, observation, "rejected")
            return state.model_dump(mode="python")

        try:
            if decision.action != "stop":
                state.tool_calls += 1
            observation = self.toolbox.execute(decision)
        except (VariantToolError, RuntimeError, ValueError) as exc:
            state.tool_errors += 1
            observation = self._rejected_observation(
                state, decision, f"Typed tool failed safely: {type(exc).__name__}."
            )
            state.last_observation = observation
            self._record(state, decision, observation, "error")
            if state.tool_errors >= self.config.max_tool_errors:
                state.complete = True
                state.stop_reason = "tool_error_limit_reached"
            return state.model_dump(mode="python")

        if decision.action == "stop":
            state.current_branch = observation.branch
            state.current_variant_count = observation.after_count
            state.last_observation = observation
            state.complete = True
            state.stop_reason = observation.feedback
            self._record(state, decision, observation, "stopped")
            return state.model_dump(mode="python")

        if decision.action in REDUCTION_ACTIONS and (
            observation.after_count == 0
            or (
                state.initial_variant_count >= state.minimum_candidate_count
                and observation.after_count < state.minimum_candidate_count
            )
        ):
            self.toolbox.delete_branch(observation.branch)
            state.no_reduction_attempts += 1
            rejected = self._rejected_observation(
                state,
                decision,
                "Safety guard rejected a branch below the minimum candidate floor; "
                "the source branch remains intact.",
            )
            state.last_observation = rejected
            state.candidate_sets = self.toolbox.candidate_summaries()
            self._record(state, decision, rejected, "rejected")
            if state.no_reduction_attempts >= self.config.max_no_reduction_attempts:
                state.complete = True
                state.stop_reason = "no_meaningful_reduction_limit_reached"
            return state.model_dump(mode="python")

        if decision.action in REDUCTION_ACTIONS:
            if observation.after_count >= observation.before_count:
                state.no_reduction_attempts += 1
            else:
                state.no_reduction_attempts = 0
        state.current_branch = observation.branch
        state.current_variant_count = observation.after_count
        state.last_observation = observation
        state.candidate_sets = self.toolbox.candidate_summaries()
        self._record(state, decision, observation, "executed")

        if state.no_reduction_attempts >= self.config.max_no_reduction_attempts:
            state.complete = True
            state.stop_reason = "no_meaningful_reduction_limit_reached"
        elif state.tool_calls >= state.max_tool_calls:
            state.complete = True
            state.stop_reason = "maximum_tool_calls_reached"
        return state.model_dump(mode="python")

    @staticmethod
    def _route_after_tool(
        raw: VariantFilteringState | dict[str, Any],
    ) -> Literal["variant_agent", "finalize"]:
        return "finalize" if _validated_state(raw).complete else "variant_agent"

    @staticmethod
    def _finalize(raw: VariantFilteringState | dict[str, Any]) -> dict[str, Any]:
        state = _validated_state(raw)
        if not state.complete:
            state.complete = True
            state.stop_reason = state.stop_reason or "graph_completed"
        return state.model_dump(mode="python")

    def _build_graph(self) -> Any:
        builder = StateGraph(VariantFilteringState)
        builder.add_node("initial_inspection", self._initial_inspection)
        builder.add_node("variant_agent", self._agent_decision)
        builder.add_node("execute_tool", self._execute_tool)
        builder.add_node("finalize", self._finalize)
        builder.add_edge(START, "initial_inspection")
        builder.add_edge("initial_inspection", "variant_agent")
        builder.add_edge("variant_agent", "execute_tool")
        builder.add_conditional_edges(
            "execute_tool",
            self._route_after_tool,
            {"variant_agent": "variant_agent", "finalize": "finalize"},
        )
        builder.add_edge("finalize", END)
        return builder.compile()

    def run(self) -> FilteringRunResult:
        process = psutil.Process()
        rss_start = process.memory_info().rss / (1024**2)
        initial_count = len(self.toolbox.candidate_ids("all"))
        initial_state = VariantFilteringState(
            run_id=self.run_id,
            initial_variant_count=initial_count,
            current_variant_count=initial_count,
            max_iterations=self.config.max_iterations,
            max_tool_calls=self.config.max_tool_calls,
            target_candidate_count=self.config.target_candidate_count,
            minimum_candidate_count=self.config.minimum_candidate_count,
        )
        raw_result = self.graph.invoke(
            initial_state,
            config={"recursion_limit": self.config.max_iterations * 3 + 10},
        )
        state = _validated_state(raw_result)
        final_ids = self.toolbox.candidate_ids(state.current_branch)
        causal_preserved = (
            self.causal_variant_id in final_ids if self.causal_variant_id is not None else None
        )
        rss_end = process.memory_info().rss / (1024**2)
        ratio = len(final_ids) / initial_count if initial_count else 0
        metrics = FilteringMetrics(
            run_id=self.run_id,
            initial_candidates=initial_count,
            final_candidates=len(final_ids),
            iterations=state.iteration,
            tool_calls=state.tool_calls,
            causal_variant_preserved=causal_preserved,
            reduction_ratio=ratio,
            stop_reason=state.stop_reason or "unknown",
            final_branch=state.current_branch,
            process_rss_mb_start=round(rss_start, 2),
            process_rss_mb_end=round(rss_end, 2),
        )
        self.audit.write_metrics(metrics)
        self.audit.write_candidates(final_ids)
        self.audit.write_event(
            "workflow_completed",
            run_id=self.run_id,
            data=metrics.model_dump(mode="json"),
        )
        return FilteringRunResult(
            state=state,
            metrics=metrics,
            final_candidate_ids=final_ids,
            audit_path=self.audit.audit_path,
            metrics_path=self.audit.metrics_path,
            candidates_path=self.audit.candidates_path,
        )
