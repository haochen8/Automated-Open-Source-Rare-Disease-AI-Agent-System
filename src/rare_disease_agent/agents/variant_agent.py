"""Provider-neutral variant filtering planner."""

from __future__ import annotations

import json

from rare_disease_agent.agents.prompts import (
    PROMPT_VERSION,
    load_variant_filtering_prompt,
    variant_filtering_prompt_hash,
)
from rare_disease_agent.agents.schemas import AgentDecision, VariantFilteringState
from rare_disease_agent.llm.base import LLMBackend
from rare_disease_agent.llm.schemas import GenerationRequest


class VariantFilteringAgent:
    def __init__(self, backend: LLMBackend) -> None:
        self.backend = backend
        self.system_prompt = load_variant_filtering_prompt()
        self.prompt_version = PROMPT_VERSION
        self.prompt_hash = variant_filtering_prompt_hash()

    def decide(self, state: VariantFilteringState) -> AgentDecision:
        context = {
            "run_id": state.run_id,
            "iteration": state.iteration + 1,
            "budgets": {
                "max_iterations": state.max_iterations,
                "max_tool_calls": state.max_tool_calls,
                "tool_calls_used": state.tool_calls,
                "target_candidate_count": state.target_candidate_count,
                "minimum_candidate_count": state.minimum_candidate_count,
            },
            "current_branch": state.current_branch,
            "current_variant_count": state.current_variant_count,
            "phenotype_available": state.phenotype_available,
            "phenotype_evaluated": state.phenotype_evaluated,
            "patient_hpo_count": state.patient_hpo_count,
            "top_phenotype_genes": [
                item.model_dump(mode="json") for item in state.top_phenotype_genes
            ],
            "pedigree_available": state.pedigree_available,
            "inheritance_available": state.inheritance_available,
            "inheritance_evaluated": state.inheritance_evaluated,
            "inheritance_summary": [
                item.model_dump(mode="json") for item in state.inheritance_summary
            ],
            "candidate_sets": [summary.model_dump(mode="json") for summary in state.candidate_sets],
            "last_observation": (
                state.last_observation.model_dump(mode="json") if state.last_observation else None
            ),
            "previous_action_signatures": state.decision_signatures,
        }
        request = GenerationRequest(
            system_prompt=self.system_prompt,
            prompt=(
                "Choose exactly one next action. Return only an AgentDecision matching the JSON "
                "schema. JSON schema:\n"
                + json.dumps(AgentDecision.model_json_schema(), sort_keys=True)
                + "\nStructured workflow context:\n"
                + json.dumps(context, sort_keys=True)
            ),
            temperature=0,
            max_tokens=1_024,
            metadata={"prompt_version": self.prompt_version, "prompt_hash": self.prompt_hash},
        )
        return self.backend.generate_structured(request, AgentDecision)
