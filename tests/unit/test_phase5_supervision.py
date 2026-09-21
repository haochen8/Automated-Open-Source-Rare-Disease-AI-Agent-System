"""Synthetic supervisor regressions; worker receipt tests mock scientific execution."""

import json
import os
import runpy
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from typer.testing import CliRunner

from rare_disease_agent.workflows import phase5
from rare_disease_agent.workflows import phase5_cli as cli
from rare_disease_agent.workflows.phase5_diagnostics import (
    Phase5IdentityMismatch,
    Phase5StageIntegrityError,
)


@pytest.fixture
def supervision(tmp_path, monkeypatch):
    class Process:
        pid = 123
        returncode = 0
        polls = 0
        waits = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def poll(self):
            self.polls += 1
            return None if self.polls <= 2 else self.returncode

        def wait(self):
            self.waits += 1
            return self.returncode

    process = Process()
    output = tmp_path / "output"
    config = tmp_path / "synthetic.json"
    config.write_text("{}")
    invocation_id = UUID(int=1).hex
    monkeypatch.setattr(cli, "uuid4", lambda: UUID(invocation_id))
    killed = []
    child = SimpleNamespace(memory_info=lambda: SimpleNamespace(rss=1024))
    parent = SimpleNamespace(
        children=lambda recursive: [child], memory_info=lambda: SimpleNamespace(rss=2048)
    )
    monkeypatch.setattr(
        cli, "load_spec", lambda path: SimpleNamespace(output=output, run_id="synthetic")
    )
    monkeypatch.setattr(cli.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(cli.psutil, "Process", lambda pid: parent)
    monkeypatch.setattr(cli.psutil, "disk_usage", lambda path: SimpleNamespace(free=30 * 1024**3))
    monkeypatch.setattr(
        cli.psutil, "virtual_memory", lambda: SimpleNamespace(available=2 * 1024**3)
    )
    monkeypatch.setattr(cli.os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(cli.time, "monotonic", lambda: 0)
    return SimpleNamespace(
        process=process,
        parent=parent,
        child=child,
        config=config,
        output=output,
        killed=killed,
        invocation_id=invocation_id,
        receipt_path=output.parent / ("phase5-" + invocation_id + "-failure.json"),
    )


@pytest.mark.parametrize("location", ["construct", "children", "parent_rss", "child_rss"])
@pytest.mark.parametrize("kind", ["psutil", "os"])
def test_inspection_denial_stops_worker_and_is_sanitized(supervision, monkeypatch, location, kind):
    state = supervision

    def denied(*args, **kwargs):
        if kind == "psutil":
            raise cli.psutil.AccessDenied(pid=987654, name="PRIVATE_SENTINEL")
        raise PermissionError(1, "PRIVATE_SENTINEL", "/synthetic/PRIVATE_SENTINEL")

    if location == "construct":
        monkeypatch.setattr(cli.psutil, "Process", denied)
    elif location == "children":
        state.parent.children = denied
    else:
        target = state.parent if location == "parent_rss" else state.child
        target.memory_info = denied

    with pytest.raises(cli.ProcessInspectionDenied) as error:
        cli.supervise(state.config)
    assert str(error.value) == cli.PROCESS_INSPECTION_DENIED
    assert error.value.__suppress_context__
    assert state.killed == [(123, cli.signal.SIGKILL)]
    assert state.process.waits == 1
    assert not list(state.output.iterdir())

    state.process.polls = 0
    result = CliRunner().invoke(cli.app, ["run", str(state.config)])
    assert result.exit_code == 1
    assert result.output == "Private Phase 5 failed: " + cli.PROCESS_INSPECTION_DENIED + "\n"
    assert "PRIVATE_SENTINEL" not in result.output
    assert "987654" not in result.output
    assert not list(state.output.iterdir())


@pytest.mark.parametrize("limit", ["rss", "output", "time", "free_disk", "available_memory"])
def test_resource_limits_still_fail_closed(supervision, monkeypatch, limit):
    state = supervision
    if limit == "rss":
        state.child.memory_info = lambda: SimpleNamespace(rss=3 * 1024**3)
    elif limit == "output":
        state.output.mkdir()
        with (state.output / "synthetic-sparse").open("wb") as stream:
            stream.truncate(2 * 1024**3 + 1)
    elif limit == "time":
        ticks = iter([0, 3601])
        monkeypatch.setattr(cli.time, "monotonic", lambda: next(ticks))
    elif limit == "free_disk":
        monkeypatch.setattr(cli.psutil, "disk_usage", lambda path: SimpleNamespace(free=0))
    else:
        monkeypatch.setattr(cli.psutil, "virtual_memory", lambda: SimpleNamespace(available=0))
    with pytest.raises(RuntimeError, match="Private rehearsal resource boundary reached") as error:
        cli.supervise(state.config)
    assert not isinstance(error.value, cli.ProcessInspectionDenied)
    assert state.killed == [(123, cli.signal.SIGKILL)]
    assert state.process.waits == 1
    assert not list(state.output.glob("supervision-*.json"))


@pytest.mark.parametrize("exited", [None, cli.psutil.NoSuchProcess, cli.psutil.ZombieProcess])
def test_success_and_process_exit_race_remain_supported(supervision, exited):
    state = supervision
    if exited:

        def gone():
            raise exited(123)

        state.child.memory_info = gone
    result = cli.supervise(state.config)
    assert result["peak_sampled_rss_bytes"] == (2048 if exited else 3072)
    assert result["process_returncode"] == 0
    assert not state.killed
    (receipt,) = state.output.glob("supervision-*.json")
    assert json.loads(receipt.read_text()) == result


def test_unrelated_permission_error_keeps_generic_private_diagnostic(supervision, monkeypatch):
    def denied(path):
        raise PermissionError("PRIVATE_SENTINEL")

    monkeypatch.setattr(cli.psutil, "disk_usage", denied)
    result = CliRunner().invoke(cli.app, ["run", str(supervision.config)])
    assert result.exit_code == 1
    assert result.output == "Private Phase 5 failed: PermissionError\n"
    assert supervision.killed == [(123, cli.signal.SIGKILL)]
    assert supervision.process.waits == 1


@pytest.mark.parametrize(
    "payload,expected_codes",
    [
        ({"identity_mismatch": ["inputs.challenge_phenotype"]}, ["inputs.challenge_phenotype"]),
        ({"identity_mismatch": ["SYNTHETIC_PRIVATE_SENTINEL"]}, ["configuration"]),
        ({"identity_mismatch": [{"SYNTHETIC_PRIVATE_SENTINEL": 1}]}, ["configuration"]),
        ({"identity_mismatch": "SYNTHETIC_PRIVATE_SENTINEL"}, ["configuration"]),
        ({"identity_mismatch": ["code_sha256"] * 100}, ["configuration"]),
        (["SYNTHETIC_PRIVATE_SENTINEL"], ["configuration"]),
        (None, ["configuration"]),
    ],
)
def test_cli_renders_only_allowlisted_worker_codes(supervision, payload, expected_codes):
    state = supervision
    state.process.returncode = cli.WORKER_IDENTITY_MISMATCH
    if payload is not None:
        if isinstance(payload, dict):
            payload = {**payload, "invocation_id": state.invocation_id}
        state.receipt_path.write_text(json.dumps(payload))
    result = CliRunner().invoke(cli.app, ["run", str(state.config)])
    assert result.exit_code == 1
    assert result.output == str(Phase5IdentityMismatch(expected_codes)) + "\n"
    assert "SYNTHETIC_PRIVATE_SENTINEL" not in result.output
    assert str(state.output.parent) not in result.output
    assert not list(state.output.glob("supervision-*.json"))


@pytest.mark.parametrize("receipt", ["invalid JSON", " " * 65537, "\udcff"])
def test_unreadable_worker_receipt_has_safe_fallback(supervision, receipt):
    supervision.process.returncode = cli.WORKER_IDENTITY_MISMATCH
    supervision.receipt_path.write_bytes(receipt.encode("utf-8", errors="surrogateescape"))
    result = CliRunner().invoke(cli.app, ["run", str(supervision.config)])
    assert result.exit_code == 1
    assert result.output == str(Phase5IdentityMismatch(None)) + "\n"


def test_unrelated_worker_failure_does_not_reuse_stale_identity_diagnostic(supervision):
    supervision.process.returncode = 1
    supervision.receipt_path.write_text(
        json.dumps(
            {"invocation_id": supervision.invocation_id, "identity_mismatch": ["code_sha256"]}
        )
    )
    result = CliRunner().invoke(cli.app, ["run", str(supervision.config)])
    assert result.exit_code == 1
    assert result.output == "Private Phase 5 failed: RuntimeError\n"


@pytest.mark.parametrize("invocation_id", [None, UUID(int=2).hex])
def test_mismatched_receipt_invocation_keeps_generic_refusal(supervision, invocation_id):
    supervision.process.returncode = cli.WORKER_IDENTITY_MISMATCH
    supervision.receipt_path.write_text(
        json.dumps({"invocation_id": invocation_id, "identity_mismatch": ["hpo_manifest_sha256"]})
    )
    result = CliRunner().invoke(cli.app, ["run", str(supervision.config)])
    assert result.exit_code == 1
    assert result.output == str(Phase5IdentityMismatch(None)) + "\n"


@pytest.mark.parametrize(
    "diagnostic,stage,reason",
    [
        ({"stage": "ingest", "reason": "missing"}, "ingest", "missing"),
        ({"stage": "analysis", "reason": "checksum"}, "analysis", "checksum"),
        ({"stage": "deliverables", "reason": "inventory"}, "deliverables", "inventory"),
        ({"stage": "prepare", "reason": "unreadable"}, "prepare", "unreadable"),
        ({"stage": "SYNTHETIC_PRIVATE_SENTINEL", "reason": "bad"}, "unknown", "unknown"),
        ({"stage": ["prepare"], "reason": {"checksum": 1}}, "unknown", "unknown"),
        ("SYNTHETIC_PRIVATE_SENTINEL", "unknown", "unknown"),
        (None, "unknown", "unknown"),
    ],
)
def test_cli_renders_only_allowlisted_stage_receipt(supervision, diagnostic, stage, reason):
    supervision.process.returncode = cli.WORKER_STAGE_INTEGRITY
    supervision.receipt_path.write_text(
        json.dumps(
            {
                "invocation_id": supervision.invocation_id,
                "stage_integrity": diagnostic,
                "errors": [{"message": "SYNTHETIC_PRIVATE_SENTINEL"}],
                "identity_mismatch": ["code_sha256"],
            }
        )
    )
    result = CliRunner().invoke(cli.app, ["run", str(supervision.config)])
    assert result.exit_code == 1
    assert result.output == str(Phase5StageIntegrityError(stage, reason)) + "\n"
    assert "SYNTHETIC_PRIVATE_SENTINEL" not in result.output
    assert str(supervision.output.parent) not in result.output
    assert not list(supervision.output.glob("supervision-*.json"))


@pytest.mark.parametrize(
    "receipt", ["absent", "malformed", "oversized", "encoding", "stale", "list"]
)
def test_invalid_stage_receipt_retains_safe_recovery_guidance(supervision, receipt):
    supervision.process.returncode = cli.WORKER_STAGE_INTEGRITY
    payloads = {
        "malformed": b"invalid JSON",
        "oversized": b" " * 65537,
        "encoding": b"\xff",
        "list": b"[]",
        "stale": json.dumps(
            {
                "invocation_id": UUID(int=2).hex,
                "stage_integrity": {"stage": "ingest", "reason": "missing"},
            }
        ).encode(),
    }
    if receipt != "absent":
        supervision.receipt_path.write_bytes(payloads[receipt])
    result = CliRunner().invoke(cli.app, ["run", str(supervision.config)])
    assert result.exit_code == 1
    assert result.output == str(Phase5StageIntegrityError()) + "\n"


def test_unrelated_worker_failure_does_not_use_stage_receipt(supervision):
    supervision.process.returncode = 1
    supervision.receipt_path.write_text(
        json.dumps(
            {
                "invocation_id": supervision.invocation_id,
                "stage_integrity": {"stage": "ingest", "reason": "missing"},
            }
        )
    )
    result = CliRunner().invoke(cli.app, ["run", str(supervision.config)])
    assert result.exit_code == 1
    assert result.output == "Private Phase 5 failed: RuntimeError\n"


@pytest.mark.parametrize("same_output", [False, True])
def test_interleaved_sibling_workers_keep_their_own_diagnostics(tmp_path, monkeypatch, same_output):
    """Run the real receipt writers in a forced A-write, B-write, A-read interleaving."""
    first_config = tmp_path / "first.json"
    second_config = tmp_path / "second.json"
    specs = {
        first_config: SimpleNamespace(output=tmp_path / "first", run_id="synthetic-shared"),
        second_config: SimpleNamespace(
            output=tmp_path / ("first" if same_output else "second"), run_id="synthetic-shared"
        ),
    }
    expected = {
        first_config: ["inputs.challenge_phenotype"],
        second_config: ["hpo_manifest_sha256"],
    }
    observed = {}
    commands = []
    monkeypatch.setattr(cli, "load_spec", lambda path: specs[path])

    def fail(spec):
        config = next(path for path, item in specs.items() if item is spec)
        raise Phase5IdentityMismatch(expected[config])

    monkeypatch.setattr(phase5, "phase5_run", fail)

    class Process:
        returncode = cli.WORKER_IDENTITY_MISMATCH

        def __init__(self, command, **kwargs):
            self.config = Path(command[3])
            commands.append(command)
            # Execute worker receipt publication without launching a process or doing science.
            previous_umask = os.umask(0o077)
            try:
                with monkeypatch.context() as worker_patch:
                    worker_patch.setattr(cli.sys, "argv", command[2:])
                    with pytest.raises(SystemExit) as caught:
                        runpy.run_module(command[2], run_name="__main__")
                    assert caught.value.code == self.returncode
            finally:
                os.umask(previous_umask)

        def __enter__(self):
            if self.config == first_config:
                # B publishes after A, but before A's supervisor reads its receipt.
                with pytest.raises(Phase5IdentityMismatch) as caught:
                    cli.supervise(second_config)
                observed[second_config] = caught.value.codes
            return self

        def __exit__(self, *args):
            pass

        def poll(self):
            return self.returncode

        def wait(self):
            return self.returncode

    monkeypatch.setattr(cli.subprocess, "Popen", Process)
    with pytest.raises(Phase5IdentityMismatch) as caught:
        cli.supervise(first_config)
    observed[first_config] = caught.value.codes
    assert len(commands) == 2
    assert observed == expected
    assert commands[0][4] != commands[1][4]
    assert len(list(tmp_path.glob("phase5-*-failure.json"))) == 2
    assert all(not list(spec.output.iterdir()) for spec in specs.values())
