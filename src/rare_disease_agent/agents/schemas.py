"""Validated decisions, audit records, and state for variant filtering."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

AgentAction = Literal[
    "inspect_statistics",
    "count_variants",
    "sample_candidates",
    "filter_quality",
    "filter_frequency",
    "filter_consequence",
    "filter_gene",
    "filter_clinvar",
    "create_rescue_branch",
    "merge_branches",
    "stop",
]
RiskLevel = Literal["low", "medium", "high"]
AuditOutcome = Literal["executed", "rejected", "error", "stopped"]


class ToolParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BranchParameters(ToolParameters):
    branch: str = Field(default="all", pattern=r"^[A-Za-z0-9_-]{1,64}$")


class SampleCandidatesParameters(BranchParameters):
    limit: int = Field(default=5, ge=1, le=25)


class FilterBranchParameters(ToolParameters):
    source_branch: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    target_branch: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")

    @model_validator(mode="after")
    def target_must_differ(self) -> FilterBranchParameters:
        if self.source_branch == self.target_branch:
            raise ValueError("target_branch must differ from source_branch")
        return self


class FilterQualityParameters(FilterBranchParameters):
    minimum_quality: float = Field(ge=0, le=10_000)


class FilterFrequencyParameters(FilterBranchParameters):
    maximum_allele_frequency: float = Field(gt=0, le=0.05)
    preserve_pathogenic_clinvar: bool = True


class FilterConsequenceParameters(FilterBranchParameters):
    allowed_consequences: list[str] = Field(min_length=1, max_length=30)


class FilterGeneParameters(FilterBranchParameters):
    genes: list[str] = Field(min_length=1, max_length=500)


class FilterClinvarParameters(FilterBranchParameters):
    classifications: list[str] = Field(min_length=1, max_length=20)


class CreateRescueBranchParameters(FilterBranchParameters):
    pass


class MergeBranchesParameters(ToolParameters):
    branches: list[str] = Field(min_length=2, max_length=20)
    target_branch: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")

    @model_validator(mode="after")
    def target_must_be_new(self) -> MergeBranchesParameters:
        if self.target_branch in self.branches:
            raise ValueError("target_branch cannot also be a source branch")
        return self


class StopParameters(BranchParameters):
    reason: str = Field(default="candidate_set_sufficient", min_length=3, max_length=200)


ACTION_PARAMETER_MODELS: dict[str, type[ToolParameters]] = {
    "inspect_statistics": BranchParameters,
    "count_variants": BranchParameters,
    "sample_candidates": SampleCandidatesParameters,
    "filter_quality": FilterQualityParameters,
    "filter_frequency": FilterFrequencyParameters,
    "filter_consequence": FilterConsequenceParameters,
    "filter_gene": FilterGeneParameters,
    "filter_clinvar": FilterClinvarParameters,
    "create_rescue_branch": CreateRescueBranchParameters,
    "merge_branches": MergeBranchesParameters,
    "stop": StopParameters,
}


class AgentDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: AgentAction
    rationale: str = Field(min_length=3, max_length=2_000)
    parameters: dict[str, Any] = Field(default_factory=dict)
    expected_effect: str = Field(min_length=3, max_length=1_000)
    risk_of_false_negative: RiskLevel

    @model_validator(mode="after")
    def validate_action_parameters(self) -> AgentDecision:
        parameter_model = ACTION_PARAMETER_MODELS[self.action]
        validated = parameter_model.model_validate(self.parameters)
        self.parameters = validated.model_dump(mode="json")
        return self

    def typed_parameters(self) -> ToolParameters:
        return ACTION_PARAMETER_MODELS[self.action].model_validate(self.parameters)

    def signature(self) -> str:
        parameters = dict(self.parameters)
        if self.action in {
            "filter_quality",
            "filter_frequency",
            "filter_consequence",
            "filter_gene",
            "filter_clinvar",
            "merge_branches",
        }:
            parameters.pop("target_branch", None)
        for key, value in parameters.items():
            if isinstance(value, list) and all(isinstance(item, str) for item in value):
                parameters[key] = sorted(set(value))
        return json.dumps(
            {"action": self.action, "parameters": parameters},
            sort_keys=True,
            separators=(",", ":"),
        )


class CandidateSetSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    branch: str
    count: int = Field(ge=0)
    parent_branch: str | None = None
    operations: list[str] = Field(default_factory=list)


class ToolObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: AgentAction | Literal["initial_inspection", "invalid_decision"]
    branch: str
    before_count: int = Field(ge=0)
    after_count: int = Field(ge=0)
    feedback: str
    data: dict[str, Any] = Field(default_factory=dict)


class FilterDecisionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp: datetime
    run_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    iteration: int = Field(ge=1)
    agent: str = "variant_agent"
    action: AgentAction
    parameters: dict[str, Any]
    rationale: str
    expected_effect: str
    risk_of_false_negative: RiskLevel
    before_count: int = Field(ge=0)
    after_count: int = Field(ge=0)
    branch: str
    model: str
    backend: str
    prompt_version: str
    prompt_hash: str
    outcome: AuditOutcome
    feedback: str


class VariantFilteringState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    initial_variant_count: int = Field(ge=0)
    current_variant_count: int = Field(ge=0)
    phenotype_available: bool = False
    inheritance_available: bool = False
    iteration: int = Field(default=0, ge=0)
    max_iterations: int = Field(default=10, ge=1)
    max_tool_calls: int = Field(default=20, ge=1)
    target_candidate_count: int = Field(default=250, ge=1)
    minimum_candidate_count: int = Field(default=25, ge=1)
    tool_calls: int = Field(default=0, ge=0)
    invalid_decisions: int = Field(default=0, ge=0)
    tool_errors: int = Field(default=0, ge=0)
    repeated_decisions: int = Field(default=0, ge=0)
    no_reduction_attempts: int = Field(default=0, ge=0)
    current_branch: str = "all"
    filter_history: list[FilterDecisionRecord] = Field(default_factory=list)
    candidate_sets: list[CandidateSetSummary] = Field(default_factory=list)
    decision_signatures: list[str] = Field(default_factory=list)
    pending_decision: AgentDecision | None = None
    last_observation: ToolObservation | None = None
    complete: bool = False
    stop_reason: str | None = None


class FilteringMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    initial_candidates: int = Field(ge=0)
    final_candidates: int = Field(ge=0)
    iterations: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    causal_variant_preserved: bool | None = None
    causal_variant_rank: int | None = Field(default=None, ge=1)
    reduction_ratio: float = Field(ge=0, le=1)
    stop_reason: str
    final_branch: str
    process_rss_mb_start: float = Field(ge=0)
    process_rss_mb_end: float = Field(ge=0)
