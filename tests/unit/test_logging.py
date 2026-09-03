from rare_disease_agent.logging import redact_sensitive_fields


def test_sensitive_structured_fields_are_redacted() -> None:
    event = redact_sensitive_fields(
        None,
        "info",
        {
            "event": "variant_filter_completed",
            "patient_id": "restricted",
            "genotype": "0/1",
            "candidate_count": 7,
        },
    )

    assert event["event"] == "variant_filter_completed"
    assert event["patient_id"] == "[REDACTED]"
    assert event["genotype"] == "[REDACTED]"
    assert event["candidate_count"] == 7
