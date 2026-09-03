import pytest
from pydantic import ValidationError

from rare_disease_agent.agents.schemas import (
    AgentDecision,
    FilterFrequencyParameters,
)


def decision(**overrides) -> dict:
    value = {
        "action": "filter_frequency",
        "rationale": "Use a reversible rarity branch.",
        "parameters": {
            "source_branch": "all",
            "target_branch": "rare",
            "maximum_allele_frequency": 0.01,
        },
        "expected_effect": "Remove common variants.",
        "risk_of_false_negative": "medium",
    }
    value.update(overrides)
    return value


def test_decision_validates_action_specific_parameters() -> None:
    parsed = AgentDecision.model_validate(decision())

    assert isinstance(parsed.typed_parameters(), FilterFrequencyParameters)
    assert parsed.parameters["preserve_pathogenic_clinvar"] is True


def test_decision_rejects_unsafe_frequency_threshold() -> None:
    payload = decision()
    payload["parameters"]["maximum_allele_frequency"] = 0.5

    with pytest.raises(ValidationError, match="less than or equal to 0.05"):
        AgentDecision.model_validate(payload)


def test_decision_rejects_unknown_parameters() -> None:
    payload = decision()
    payload["parameters"]["sql"] = "DROP TABLE variants"

    with pytest.raises(ValidationError, match="Extra inputs"):
        AgentDecision.model_validate(payload)


def test_filter_target_must_be_new_branch() -> None:
    payload = decision()
    payload["parameters"]["target_branch"] = "all"

    with pytest.raises(ValidationError, match="must differ"):
        AgentDecision.model_validate(payload)


def test_signature_ignores_free_form_rationale() -> None:
    first = AgentDecision.model_validate(decision())
    second = AgentDecision.model_validate(decision(rationale="A different explanation."))

    assert first.signature() == second.signature()


def test_signature_detects_same_filter_with_renamed_target() -> None:
    first = AgentDecision.model_validate(decision())
    payload = decision()
    payload["parameters"]["target_branch"] = "rare_again"
    second = AgentDecision.model_validate(payload)

    assert first.signature() == second.signature()
