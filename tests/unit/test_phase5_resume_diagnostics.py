"""Resume refusal and privacy regressions using only local synthetic preparation."""

import copy
import json
import os
import shutil
import subprocess
import sys
import traceback
from pathlib import Path
from uuid import uuid4

import pytest
from test_phase5_ingestion import phase5_fixture

from rare_disease_agent.resource_management import sha256
from rare_disease_agent.workflows import phase5
from rare_disease_agent.workflows.phase5_diagnostics import (
    IDENTITY_FIELDS,
    LABELS,
    STAGE_LABELS,
    WORKER_IDENTITY_MISMATCH,
    WORKER_STAGE_INTEGRITY,
    Phase5IdentityMismatch,
    Phase5StageIntegrityError,
    describe_mismatch,
)
from rare_disease_agent.workflows.recovery import (
    CommittedStageIntegrityError,
    RestartableRun,
    RunIdentityMismatch,
)


def snapshot(directory):
    return {
        str(p.relative_to(directory)): p.read_bytes() for p in directory.rglob("*") if p.is_file()
    }


@pytest.fixture
def prepared(tmp_path):
    spec = phase5_fixture(tmp_path)
    with pytest.raises(RuntimeError, match="InterruptedError"):
        phase5.phase5_run(spec, interrupt_after="prepare")
    return spec


@pytest.mark.parametrize(
    "change,label",
    [
        ("original_vcf", "annotation rehearsal source VCF checksum"),
        ("original_index", "original index fingerprint"),
        ("phenotype_docx", "phenotype document fingerprint"),
        ("mtime", "original VCF fingerprint"),
        ("path", "original index path identity"),
        ("dataset_revision", "dataset revision identity"),
        ("run_id", "run ID"),
        ("confirmed_affected_sample", "affected-sample confirmation"),
        ("hpo", "HPO resource manifest identity"),
        ("annotation", "annotation rehearsal integrity manifest identity"),
        ("code", "source-code identity"),
    ],
)
def test_prepared_resume_reports_identity_without_mutation_or_disclosure(
    prepared, monkeypatch, change, label
):
    spec = prepared
    before = snapshot(spec.output)
    if change in {"original_vcf", "original_index", "phenotype_docx"}:
        with getattr(spec, change).open("ab") as stream:
            stream.write(b"SYNTHETIC_PRIVATE_SENTINEL")
    elif change == "mtime":
        info = spec.original_vcf.stat()
        os.utime(spec.original_vcf, ns=(info.st_atime_ns, info.st_mtime_ns + 1000000))
    elif change == "path":
        # Preserve content, size and timestamp so only the configured path differs.
        import shutil

        alternate = spec.original_index.with_name("SYNTHETIC_PRIVATE_SENTINEL.index")
        shutil.copy2(spec.original_index, alternate)
        spec = spec.model_copy(update={"original_index": alternate})
    elif change in {"dataset_revision", "run_id", "confirmed_affected_sample"}:
        value = False if change == "confirmed_affected_sample" else "SYNTHETIC_PRIVATE_SENTINEL"
        spec = spec.model_copy(update={change: value})
    elif change in {"hpo", "annotation"}:
        manifest = (
            spec.hpo_directory / "manifest.json"
            if change == "hpo"
            else spec.annotation_rehearsal / "integrity_manifest.json"
        )
        manifest.write_text(manifest.read_text() + "\n")
    else:
        monkeypatch.setattr(phase5, "code_checksum", lambda: "SYNTHETIC_PRIVATE_SENTINEL")
    with pytest.raises(Phase5IdentityMismatch) as caught:
        phase5.phase5_run(spec)
    message = str(caught.value)
    assert label in message
    assert "fresh run in a new output directory is required" in message
    rendered = "".join(traceback.format_exception(caught.value))
    assert "SYNTHETIC_PRIVATE_SENTINEL" not in rendered
    assert str(spec.output.parent) not in rendered
    assert sha256(spec.original_vcf) not in rendered
    assert caught.value.__suppress_context__
    assert snapshot(spec.output) == before
    assert not (spec.output / "ingest").exists()


def test_worker_publishes_safe_diagnostic_after_prepared_input_change(prepared, tmp_path):
    before = snapshot(prepared.output)
    prepared.phenotype_docx.write_bytes(b"SYNTHETIC_PRIVATE_SENTINEL")
    config = tmp_path / "SYNTHETIC_PRIVATE_SENTINEL.json"
    config.write_text(prepared.model_dump_json())
    invocation_id = uuid4().hex
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "rare_disease_agent.workflows.phase5_worker",
            str(config),
            invocation_id,
        ],
        env={
            "PATH": os.defpath,
            "PYTHONPATH": str(Path(phase5.__file__).resolve().parents[2]),
            "LANGSMITH_TRACING": "false",
            "LANGCHAIN_TRACING_V2": "false",
        },
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == WORKER_IDENTITY_MISMATCH
    assert result.stdout == result.stderr == b""
    receipt = (prepared.output.parent / ("phase5-" + invocation_id + "-failure.json")).read_text()
    assert json.loads(receipt)["invocation_id"] == invocation_id
    assert json.loads(receipt)["identity_mismatch"] == ["inputs.challenge_phenotype"]
    assert "SYNTHETIC_PRIVATE_SENTINEL" not in receipt
    assert str(tmp_path) not in receipt
    assert sha256(prepared.phenotype_docx) not in receipt
    assert snapshot(prepared.output) == before


def test_unchanged_preparation_resumes_and_changed_input_can_start_fresh(prepared, tmp_path):
    before = snapshot(prepared.output)
    with pytest.raises(RuntimeError, match="InterruptedError"):
        phase5.phase5_run(prepared, interrupt_after="prepare")
    assert snapshot(prepared.output) == before
    prepared.original_index.write_text("changed synthetic index")
    fresh = prepared.model_copy(update={"output": tmp_path / "fresh", "run_id": "synthetic-fresh"})
    with pytest.raises(RuntimeError, match="InterruptedError"):
        phase5.phase5_run(fresh, interrupt_after="prepare")
    assert (fresh.output / "prepare" / "run_manifest.json").is_file()
    assert snapshot(prepared.output) == before


@pytest.mark.parametrize("corruption", ["annotation", "journal"])
def test_integrity_failures_remain_generic_and_do_not_rewrite_stages(prepared, corruption):
    target = {
        "annotation": prepared.annotation_rehearsal / "annotated.vcf",
        "journal": prepared.output / "run.json",
    }[corruption]
    target.write_text("SYNTHETIC_PRIVATE_SENTINEL")
    before = snapshot(prepared.output)
    with pytest.raises(RuntimeError) as caught:
        phase5.phase5_run(prepared)
    assert not isinstance(caught.value, Phase5IdentityMismatch)
    assert str(caught.value).startswith("Private Phase 5 stage failed:")
    assert "SYNTHETIC_PRIVATE_SENTINEL" not in str(caught.value)
    assert snapshot(prepared.output) == before


@pytest.mark.parametrize("stage", ["prepare", "ingest", "analysis", "deliverables"])
@pytest.mark.parametrize("corruption", ["missing", "directory", "checksum", "inventory"])
def test_committed_stage_refusal_is_specific_private_and_immutable(prepared, stage, corruption):
    # Commit small synthetic artifacts through the real journal, without scientific execution.
    configuration = json.loads((prepared.output / "run.json").read_text())["configuration"]
    runner = RestartableRun(prepared.output, run_id=prepared.run_id, configuration=configuration)
    if stage != "prepare":
        runner.stage(stage, lambda path: (path / "artifact.json").write_text("synthetic"))
    directory = prepared.output / stage
    target = next(path for path in directory.rglob("*") if path.is_file())
    digest = sha256(target)
    if corruption == "missing":
        target.unlink()
    elif corruption == "directory":
        shutil.rmtree(directory)
    elif corruption == "checksum":
        target.write_text("SYNTHETIC_PRIVATE_SENTINEL")
    else:
        (directory / "SYNTHETIC_PRIVATE_SENTINEL").write_text("SYNTHETIC_PRIVATE_SENTINEL")
    before = snapshot(prepared.output)
    with pytest.raises(Phase5StageIntegrityError) as caught:
        phase5.phase5_run(prepared)
    error = caught.value
    assert error.stage == stage
    assert error.reason == ("missing" if corruption == "directory" else corruption)
    assert STAGE_LABELS[stage] in str(error)
    assert "fresh run in a new output directory is required" in str(error)
    assert "do not delete or regenerate committed stages in place" in str(error)
    rendered = "".join(traceback.format_exception(error))
    for private in ("SYNTHETIC_PRIVATE_SENTINEL", str(prepared.output.parent), digest):
        assert private not in rendered
    assert error.__suppress_context__
    assert snapshot(prepared.output) == before


def test_committed_read_failure_hides_underlying_os_error(prepared, monkeypatch):
    from rare_disease_agent.workflows import recovery

    before = snapshot(prepared.output)

    def denied(path):
        raise PermissionError(13, "SYNTHETIC_PRIVATE_SENTINEL", str(path))

    monkeypatch.setattr(recovery, "sha256", denied)
    with pytest.raises(Phase5StageIntegrityError) as caught:
        phase5.phase5_run(prepared)
    assert (caught.value.stage, caught.value.reason) == ("prepare", "unreadable")
    rendered = "".join(traceback.format_exception(caught.value))
    assert "SYNTHETIC_PRIVATE_SENTINEL" not in rendered
    assert str(prepared.output.parent) not in rendered
    assert snapshot(prepared.output) == before


def test_corrupt_committed_stage_requires_fresh_output_and_preserves_identity_checks(prepared):
    (prepared.output / "prepare" / "phenotype.json").unlink()
    before = snapshot(prepared.output)
    prepared.original_index.write_text("changed synthetic index")
    # Identity refusal must still win even when a committed stage is also unusable.
    with pytest.raises(Phase5IdentityMismatch, match="original index fingerprint"):
        phase5.phase5_run(prepared)
    fresh = prepared.model_copy(update={"output": prepared.output.parent / "fresh"})
    with pytest.raises(RuntimeError, match="InterruptedError"):
        phase5.phase5_run(fresh, interrupt_after="prepare")
    assert (fresh.output / "prepare" / "phenotype.json").is_file()
    assert snapshot(prepared.output) == before


def test_phase5_stage_recheck_reports_corruption_after_runner_construction(prepared, monkeypatch):
    original = RestartableRun.stage
    before = None

    def changed_stage(runner, name, operation):
        nonlocal before
        (prepared.output / "prepare" / "phenotype.json").unlink()
        before = snapshot(prepared.output)
        return original(runner, name, operation)

    monkeypatch.setattr(RestartableRun, "stage", changed_stage)
    with pytest.raises(Phase5StageIntegrityError) as caught:
        phase5.phase5_run(prepared)
    assert (caught.value.stage, caught.value.reason) == ("prepare", "missing")
    assert snapshot(prepared.output) == before


@pytest.mark.parametrize("name", ["SYNTHETIC_PRIVATE_SENTINEL", "../SYNTHETIC_PRIVATE_SENTINEL"])
def test_untrusted_committed_stage_name_is_not_disclosed(prepared, name):
    journal = prepared.output / "run.json"
    payload = json.loads(journal.read_text())
    payload["stages"][name] = payload["stages"].pop("prepare")
    journal.write_text(json.dumps(payload))
    before = snapshot(prepared.output)
    with pytest.raises(Phase5StageIntegrityError) as caught:
        phase5.phase5_run(prepared)
    assert caught.value.stage == "unknown"
    assert "SYNTHETIC_PRIVATE_SENTINEL" not in str(caught.value)
    assert snapshot(prepared.output) == before


@pytest.mark.parametrize("entry", ["constructor", "stage", "complete"])
def test_recovery_validates_all_committed_stages_before_reuse(tmp_path, entry):
    runner = RestartableRun(tmp_path, run_id="synthetic", configuration={})
    runner.stage("prepare", lambda path: (path / "first").write_text("synthetic"))
    runner.stage("ingest", lambda path: (path / "second").write_text("synthetic"))
    (tmp_path / "ingest" / "second").unlink()
    before = snapshot(tmp_path)

    def must_not_run(path):
        pytest.fail("A committed stage must never be regenerated")

    with pytest.raises(CommittedStageIntegrityError) as caught:
        if entry == "constructor":
            RestartableRun(tmp_path, run_id="synthetic", configuration={})
        elif entry == "stage":
            runner.stage("prepare", must_not_run)
        else:
            runner.complete()
    assert (caught.value.stage, caught.value.reason) == ("ingest", "missing")
    assert snapshot(tmp_path) == before


def test_worker_publishes_only_safe_stage_diagnostic(prepared, tmp_path):
    (prepared.output / "prepare" / "phenotype.json").write_text("SYNTHETIC_PRIVATE_SENTINEL")
    before = snapshot(prepared.output)
    config = tmp_path / "SYNTHETIC_PRIVATE_SENTINEL.json"
    config.write_text(prepared.model_dump_json())
    invocation_id = uuid4().hex
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "rare_disease_agent.workflows.phase5_worker",
            str(config),
            invocation_id,
        ],
        env={
            "PATH": os.defpath,
            "PYTHONPATH": str(Path(phase5.__file__).resolve().parents[2]),
            "LANGSMITH_TRACING": "false",
            "LANGCHAIN_TRACING_V2": "false",
        },
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == WORKER_STAGE_INTEGRITY
    assert result.stdout == result.stderr == b""
    receipt = (prepared.output.parent / ("phase5-" + invocation_id + "-failure.json")).read_text()
    payload = json.loads(receipt)
    assert payload["invocation_id"] == invocation_id
    assert payload["stage_integrity"] == {"stage": "prepare", "reason": "checksum"}
    assert "identity_mismatch" not in payload
    for private in ("SYNTHETIC_PRIVATE_SENTINEL", str(tmp_path), sha256(config)):
        assert private not in receipt
    assert snapshot(prepared.output) == before


@pytest.mark.parametrize("field,label", IDENTITY_FIELDS.items())
def test_all_known_identity_fields_use_fixed_labels(field, label):
    previous = {}
    target = previous
    for key in field[:-1]:
        target = target.setdefault(key, {})
    target[field[-1]] = "SYNTHETIC_PRIVATE_SENTINEL"
    error = RunIdentityMismatch(previous, run_id_changed=False)
    diagnostic = describe_mismatch(error, {})
    assert label in str(diagnostic)
    assert "SYNTHETIC_PRIVATE_SENTINEL" not in str(diagnostic)


@pytest.mark.parametrize("mutation", ["unknown_field", "hash_only", "malformed_nested"])
def test_hash_refusal_remains_authoritative_for_unclassified_changes(tmp_path, mutation):
    configuration = {"specification": {}, "inputs": {}}
    runner = RestartableRun(tmp_path, run_id="synthetic", configuration=configuration)
    current = copy.deepcopy(configuration)
    if mutation == "unknown_field":
        current["SYNTHETIC_PRIVATE_SENTINEL"] = "SYNTHETIC_PRIVATE_SENTINEL"
    elif mutation == "hash_only":
        runner.journal.configuration_hash = "SYNTHETIC_PRIVATE_SENTINEL"
        runner.save()
    else:
        current["inputs"] = ["SYNTHETIC_PRIVATE_SENTINEL"]
    before = snapshot(tmp_path)
    with pytest.raises(RunIdentityMismatch) as caught:
        RestartableRun(tmp_path, run_id="synthetic", configuration=current)
    message = str(describe_mismatch(caught.value, current))
    assert LABELS["configuration"] in message
    assert "fresh run" in message
    assert "SYNTHETIC_PRIVATE_SENTINEL" not in message
    assert snapshot(tmp_path) == before


def test_multiple_mismatches_are_deterministic_and_generic_errors_stay_private(
    prepared, monkeypatch
):
    previous = {"code_sha256": "old", "hpo_manifest_sha256": "old"}
    diagnostic = describe_mismatch(RunIdentityMismatch(previous, run_id_changed=True), {})
    assert diagnostic.codes == ["hpo_manifest_sha256", "code_sha256", "run_id"]

    def fail(*args, **kwargs):
        raise ValueError("SYNTHETIC_PRIVATE_SENTINEL")

    monkeypatch.setattr(phase5, "_run", fail)
    with pytest.raises(RuntimeError) as caught:
        phase5.phase5_run(prepared)
    assert str(caught.value) == "Private Phase 5 stage failed: ValueError"
    assert not isinstance(caught.value, Phase5IdentityMismatch)
