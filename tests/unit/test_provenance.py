from rare_disease_agent.reporting.provenance import evidence_provenance, software_identity


def test_software_and_evidence_provenance_are_explicit() -> None:
    software = software_identity()
    evidence = evidence_provenance(
        run_id="provenance-test",
        tool_version="tool-v1",
        data_version="data-v1",
        method="deterministic",
        parameters={"threshold": 0.5},
        software=software,
    )

    assert software.git_commit
    assert software.python_version
    assert "pydantic" in software.dependencies
    assert evidence.git_commit == software.git_commit
    assert evidence.parameters == {"threshold": 0.5}
