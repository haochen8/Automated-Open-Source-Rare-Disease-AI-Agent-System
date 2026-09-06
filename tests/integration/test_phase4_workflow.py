import json

import duckdb
import pytest

from rare_disease_agent.reporting.research import CriticFacts, Reinspection, critique
from rare_disease_agent.resource_management import sha256
from rare_disease_agent.synthetic.phase4 import EDGE_CASES
from rare_disease_agent.workflows.phase4 import phase4_run
from rare_disease_agent.workflows.recovery import RestartableRun


@pytest.mark.parametrize("case", EDGE_CASES)
def test_edge_cases_have_persistent_evidence_rescue_and_redacted_reports(tmp_path, case):
    report_path = phase4_run(tmp_path, case_name=case, run_id="edge-test")
    report = json.loads(report_path.read_text())
    assert report["critic"]["passed"]
    assert "Not for clinical use" in report["warning"]
    assert 1 <= len(report["candidates"]) <= 20
    assert "PROBAND" not in report_path.read_text()
    assert "SYNTH-CAUSAL" not in report_path.read_text()
    assert "genotype" not in report_path.read_text()
    metrics = json.loads((tmp_path / "pipeline/track1_metrics.json").read_text())
    assert metrics["causal_variant_preserved"]
    assert metrics["novel_gene_rescue_preserved"]
    assert metrics["top_10"]
    assert (tmp_path / "pipeline/evidence.parquet").is_file()
    with duckdb.connect(
        str(tmp_path / "pipeline/candidate_membership.duckdb"), read_only=True
    ) as connection:
        assert connection.execute("SELECT count(*) FROM evidence").fetchone()[0] > 20
        if case in {"missing-parent", "mosaic"}:
            assert (
                connection.execute(
                    "SELECT max(score) FROM evidence WHERE variant_id='SYNTH-CAUSAL-001' "
                    "AND method='de_novo'"
                ).fetchone()[0]
                < 0.8
            )
        if case == "unphased-compound":
            pairs = [
                json.loads(row[0])
                for row in connection.execute("SELECT payload FROM compound_pairs").fetchall()
            ]
            assert any(
                pair["gene"] == "SYN_CAUSAL" and pair["phase"] == "unknown" for pair in pairs
            )


def test_interrupted_pipeline_resumes_without_duplicate_audits(tmp_path):
    with pytest.raises(InterruptedError):
        phase4_run(
            tmp_path, case_name="missing-parent", run_id="resume-test", interrupt_after="pipeline"
        )
    audit = tmp_path / "pipeline/audit_log.jsonl"
    previous = sha256(audit)
    membership = sha256(tmp_path / "pipeline/candidate_membership.duckdb")
    first = phase4_run(tmp_path, case_name="missing-parent", run_id="resume-test")
    report_hash = sha256(first)
    second = phase4_run(tmp_path, case_name="missing-parent", run_id="resume-test")
    assert sha256(second) == report_hash
    assert sha256(audit) == previous
    assert sha256(tmp_path / "pipeline/candidate_membership.duckdb") == membership
    events = [
        json.loads(line) for line in (tmp_path / "stage_audit.jsonl").read_text().splitlines()
    ]
    assert len(events) == len({event["event_id"] for event in events}) == 3
    with pytest.raises(ValueError, match="identity"):
        phase4_run(tmp_path, case_name="missing-parent", run_id="other")
    first.write_text("partial")
    with pytest.raises(ValueError, match="artifacts"):
        phase4_run(tmp_path, case_name="missing-parent", run_id="resume-test")


def test_failed_stage_recomputed_and_failure_reason_redacted(tmp_path):
    run = RestartableRun(
        tmp_path, run_id="failure", configuration={"resource_lock_hashes": ["a"], "seed": 1}
    )

    def fail(path):
        (path / "partial.txt").write_text("partial")
        raise ValueError("private sample name")

    with pytest.raises(ValueError):
        run.stage("evidence", fail)
    assert run.journal.failure_reason == "ValueError"
    assert "private sample" not in (tmp_path / "run.json").read_text()
    run.stage("evidence", lambda path: (path / "complete.txt").write_text("complete"))
    assert not (tmp_path / "evidence/partial.txt").exists()
    run.complete()
    with pytest.raises(ValueError):
        RestartableRun(
            tmp_path, run_id="failure", configuration={"resource_lock_hashes": ["stale"], "seed": 1}
        )


def test_critic_limits_and_forbidden_operations():
    facts = CriticFacts(
        complete_evidence=False,
        provenance_matches=True,
        rescue_survives=False,
        uncertain_language_present=True,
        competing_strong_models=1,
    )
    calls = []

    def inspect(request):
        calls.append(request)
        return facts

    result = critique(facts, inspect)
    assert len(calls) == result.reinspections == 2
    assert not result.passed
    assert "rescue" in result.findings
    for forbidden in ["sql", "delete_branch", "change_score"]:
        with pytest.raises(ValueError):
            Reinspection(operation=forbidden)
    with pytest.raises(ValueError):
        critique(facts, inspect, max_reinspections=3)


def test_resume_cannot_adopt_or_delete_unowned_output_directory(tmp_path):
    existing = tmp_path / "pipeline"
    existing.mkdir()
    document = existing / "keep.txt"
    document.write_text("unrelated user content")
    with pytest.raises(ValueError, match="nonempty"):
        RestartableRun(tmp_path, run_id="new", configuration={})
    assert document.read_text() == "unrelated user content"
