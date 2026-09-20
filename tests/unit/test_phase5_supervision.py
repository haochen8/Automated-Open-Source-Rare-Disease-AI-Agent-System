"""Synthetic supervisor regressions; no worker or host process inspection is performed."""

import json
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from rare_disease_agent.workflows import phase5_cli as cli


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
    killed = []
    child = SimpleNamespace(memory_info=lambda: SimpleNamespace(rss=1024))
    parent = SimpleNamespace(
        children=lambda recursive: [child], memory_info=lambda: SimpleNamespace(rss=2048)
    )
    monkeypatch.setattr(cli, "load_spec", lambda path: SimpleNamespace(output=output))
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
        process=process, parent=parent, child=child, config=config, output=output, killed=killed
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
