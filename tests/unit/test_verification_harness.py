import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "verify.py"
SPEC = importlib.util.spec_from_file_location("verification_harness", SCRIPT)
verify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify)


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "checkout with spaces"
    root.mkdir()
    subprocess.run(
        ["git", "init", "--separate-git-dir", str(tmp_path / "metadata"), str(root)],
        check=True,
        capture_output=True,
    )
    (root / ".gitignore").write_text(".verification/\n.coverage\n.pytest_cache/\ndist/\n")
    (root / "source.txt").write_text("original\n")
    verify.git(root, "add", ".gitignore", "source.txt")
    return root


def test_root_detection_supports_git_file_and_other_working_directory(repository, monkeypatch):
    assert (repository / ".git").is_file()
    monkeypatch.setattr(verify, "__file__", str(repository / "scripts" / "verify.py"))
    monkeypatch.chdir(repository.parent)
    assert verify.repository_root() == repository
    monkeypatch.setattr(verify, "__file__", str(repository / "nested" / "scripts" / "verify.py"))
    (repository / "nested").mkdir()
    with pytest.raises(ValueError, match="scripts directory"):
        verify.repository_root()


@pytest.mark.parametrize("mutation", ["content", "mode", "delete", "index", "new", "symlink"])
def test_tracked_state_detects_mutations(repository, mutation):
    source = repository / "source.txt"
    before = verify.fingerprint(repository)
    if mutation == "content":
        source.write_text("changed\n")
    elif mutation == "mode":
        source.chmod(0o755)
    elif mutation == "delete":
        source.unlink()
    elif mutation == "index":
        source.write_text("staged\n")
        verify.git(repository, "add", "source.txt")
        source.write_text("original\n")
    elif mutation == "new":
        (repository / "new.txt").write_text("new\n")
        verify.git(repository, "add", "new.txt")
    else:
        source.unlink()
        source.symlink_to("unreadable-or-missing-target")
    assert verify.fingerprint(repository) != before


def test_symlink_target_is_not_read_and_dirty_state_is_allowed(repository):
    source = repository / "source.txt"
    source.unlink()
    source.symlink_to("missing-target")
    verify.git(repository, "add", "source.txt")
    before = verify.fingerprint(repository)
    assert verify.fingerprint(repository) == before
    source.unlink()
    source.symlink_to("another-missing-target")
    assert verify.fingerprint(repository) != before


def test_generated_artifacts_allowed_unless_tracked(repository):
    before = verify.fingerprint(repository)
    (repository / ".coverage").write_text("synthetic coverage")
    assert verify.fingerprint(repository) == before
    verify.git(repository, "add", "-f", ".coverage")
    with pytest.raises(verify.IncompleteError, match="prohibited/generated"):
        verify.fingerprint(repository)


def test_execute_streams_output_without_shell(repository, monkeypatch):
    calls = []
    monkeypatch.setattr(
        verify.subprocess, "run", lambda *args, **kwargs: calls.append((args, kwargs))
    )
    command = ["/a path/python", "-m", "pytest"]
    verify.execute(command, repository, {"PYTHONPATH": "src"})
    assert calls == [((command,), {"cwd": repository, "env": {"PYTHONPATH": "src"}, "check": True})]


def prepare_main(repository, monkeypatch):
    monkeypatch.setattr(verify, "repository_root", lambda: repository)
    # Do not require a commit in this synthetic repository.
    original_git = verify.git

    def git(root, *args):
        if args == ("rev-parse", "HEAD"):
            return b"synthetic-head\n"
        if args == ("rev-parse", "--abbrev-ref", "HEAD"):
            return b"synthetic-branch\n"
        return original_git(root, *args)

    monkeypatch.setattr(verify, "git", git)


def test_main_preserves_interpreter_and_runs_exact_gate(repository, monkeypatch):
    prepare_main(repository, monkeypatch)
    selected = str(repository / "linked environment" / "bin" / "python")
    monkeypatch.setattr(sys, "executable", selected)
    monkeypatch.setenv("PYTHONPATH", "/another-checkout/src")
    monkeypatch.setenv("PYTEST_ADDOPTS", "--collect-only")
    (repository / "source.txt").write_text("pre-existing dirty change\n")
    calls = []
    monkeypatch.setattr(verify, "execute", lambda *args: calls.append(args))
    assert verify.main() == 0
    assert len(calls) == 9
    assert calls[0][0] == [selected, "-c", verify.IMPORT_PROBE, str(repository)]
    expected = [
        [selected, "-m", "pytest", "--cov=rare_disease_agent", "--cov-report=term-missing"],
        [selected, "-m", "ruff", "check", "."],
        [selected, "-m", "ruff", "format", "--check", "."],
        [selected, "scripts/privacy_guard.py", "--all"],
        [selected, "-m", "pip", "check"],
        [selected, "-m", "hatchling", "build"],
        ["git", "diff", "--check"],
        ["git", "diff", "--cached", "--check"],
    ]
    assert [call[0] for call in calls[1:]] == expected
    assert all(call[1] == repository for call in calls)
    assert all(call[2]["PYTHONPATH"] == str(repository / "src") for call in calls)
    assert all(call[2]["PYTEST_ADDOPTS"] == "" for call in calls)


@pytest.mark.parametrize("failure_index", range(9))
def test_failures_stop_gate_preserve_code_and_check_mutations(
    repository, monkeypatch, capsys, failure_index
):
    prepare_main(repository, monkeypatch)
    calls = []

    def execute(command, root, environment):
        calls.append(command)
        if len(calls) == failure_index + 1:
            (root / "source.txt").write_text("unexpected mutation\n")
            raise subprocess.CalledProcessError(7, command)

    monkeypatch.setattr(verify, "execute", execute)
    assert verify.main() == 7
    assert len(calls) == failure_index + 1
    captured = capsys.readouterr()
    assert "changed during verification" in captured.out
    assert "Verification passed" not in captured.out


@pytest.mark.parametrize(
    "error,code", [(FileNotFoundError("missing tool"), 1), (KeyboardInterrupt(), 130)]
)
def test_missing_tool_and_interrupt_fail(repository, monkeypatch, capsys, error, code):
    prepare_main(repository, monkeypatch)

    def execute(*args):
        raise error

    monkeypatch.setattr(verify, "execute", execute)
    assert verify.main() == code
    captured = capsys.readouterr()
    assert "Repository contents and index unchanged" in captured.out
    assert "Verification passed" not in captured.out


def test_mutation_alone_fails_successful_checks(repository, monkeypatch):
    prepare_main(repository, monkeypatch)
    monkeypatch.setattr(
        verify, "execute", lambda *args: (repository / "source.txt").write_text("changed\n")
    )
    assert verify.main() == 1


def test_import_probe_accepts_only_expected_package(tmp_path):
    for name in ("expected", "wrong"):
        package = tmp_path / name / "src" / "rare_disease_agent"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("")
    for name, succeeds in (("expected", True), ("wrong", False)):
        result = subprocess.run(
            [sys.executable, "-c", verify.IMPORT_PROBE, str(tmp_path / "expected")],
            cwd=tmp_path,
            env={**os.environ, "PYTHONPATH": str(tmp_path / name / "src")},
            capture_output=True,
            text=True,
        )
        assert (result.returncode == 0) is succeeds
        assert ("Verified import:" in result.stdout) is succeeds


def test_make_and_ci_quoting(tmp_path):
    root = SCRIPT.parents[1]
    (tmp_path / "Makefile").write_text((root / "Makefile").read_text())
    binary = tmp_path / "bin with spaces"
    binary.mkdir()
    python = binary / "python"
    python.write_text(f"#!{sys.executable}\nimport sys\nprint(repr(sys.argv[1:]))\n")
    python.chmod(0o755)
    default = subprocess.run(["make", "-n"], cwd=tmp_path, capture_output=True, text=True)
    assert default.returncode == 0
    assert "pip install" in default.stdout  # Dry run only; preserve the existing default target.
    expected = "['scripts/verify.py']"
    result = subprocess.run(
        ["make", "verify", f"PYTHON={python}"], cwd=tmp_path, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert expected in result.stdout
    workflow = (root / ".github" / "workflows" / "test.yml").read_text()
    invocation = 'make verify PYTHON="$(command -v python)"'
    assert f"- run: {invocation}" in workflow
    result = subprocess.run(
        ["/bin/sh", "-c", invocation],
        cwd=tmp_path,
        env={**os.environ, "PATH": str(binary) + os.pathsep + os.environ["PATH"]},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert expected in result.stdout
    result = subprocess.run(
        ["make", "verification-status", f"PYTHON={python}"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "['scripts/verify.py', '--status']" in result.stdout


def pass_record(repository, monkeypatch):
    prepare_main(repository, monkeypatch)
    monkeypatch.setattr(verify, "execute", lambda *args: None)
    assert verify.main() == 0
    assert verify.status(repository) == 0
    return verify.read_evidence(repository)


def test_fingerprint_path_and_timestamp_independence(repository, tmp_path):
    other = tmp_path / "different-location"
    other.mkdir()
    subprocess.run(["git", "init", str(other)], check=True, capture_output=True)
    for name in (".gitignore", "source.txt"):
        (other / name).write_bytes((repository / name).read_bytes())
    verify.git(other, "add", ".gitignore", "source.txt")
    before = verify.fingerprint(repository)
    os.utime(repository / "source.txt", (1000, 1000))
    assert verify.fingerprint(repository) == before == verify.fingerprint(other)


@pytest.mark.parametrize(
    "change", ["tracked", "staged", "delete", "source", "docs", "mode", "link"]
)
def test_pass_becomes_stale(repository, monkeypatch, capsys, change):
    pass_record(repository, monkeypatch)
    source = repository / "source.txt"
    if change == "tracked":
        source.write_text("changed")
    elif change == "staged":
        source.write_text("index only")
        verify.git(repository, "add", "source.txt")
        source.write_text("original\n")
    elif change == "delete":
        source.unlink()
    elif change == "source":
        (repository / "scripts").mkdir()
        (repository / "scripts" / "new.py").write_text("x = 1\n")
    elif change == "docs":
        (repository / "notes.md").write_text("New documentation\n")
    elif change == "mode":
        source.chmod(0o755)
    else:
        source.unlink()
        source.symlink_to("missing")
    assert verify.status(repository) == 1
    assert "STALE: recorded execution PASS" in capsys.readouterr().out


def test_untracked_changes_and_removal_invalidate(repository, monkeypatch):
    source = repository / "notes.md"
    source.write_text("first")
    pass_record(repository, monkeypatch)
    source.write_text("second")
    assert verify.status(repository) == 1
    source.write_text("first")
    assert verify.status(repository) == 0
    source.unlink()
    assert verify.status(repository) == 1


def test_generated_outputs_and_receipt_do_not_invalidate(repository, monkeypatch):
    record = pass_record(repository, monkeypatch)
    (repository / ".coverage").write_text("new coverage")
    (repository / "dist").mkdir()
    (repository / "dist" / "ignored.whl").write_text("generated")
    record["ended_at"] = verify.now()
    verify.publish(repository, record)
    assert verify.status(repository) == 0


@pytest.mark.parametrize("name", ["unknown.bin", "private_data/patient.txt", "sample.vcf", ".env"])
def test_refused_inventory_is_not_read(repository, monkeypatch, name):
    path = repository / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("synthetic sentinel, never private data")
    if name != "unknown.bin":
        verify.git(repository, "add", "-f", name)

    def never_read(*args):
        pytest.fail("Inventory must be refused before reading any files")

    monkeypatch.setattr(verify, "file_identity", never_read)
    with pytest.raises(verify.IncompleteError):
        verify.fingerprint(repository)


def test_symlink_target_and_ancestor_not_followed(repository, tmp_path):
    target = tmp_path / "external"
    target.mkdir()
    (target / "file.py").write_text("first")
    scripts = repository / "scripts"
    scripts.mkdir()
    link = scripts / "link.py"
    link.symlink_to(target / "file.py")
    before = verify.fingerprint(repository)
    (target / "file.py").write_text("target changed")
    assert verify.fingerprint(repository) == before
    verify.git(repository, "add", "scripts/link.py")
    link.unlink()
    scripts.rmdir()
    scripts.symlink_to(target, target_is_directory=True)
    with pytest.raises(verify.IncompleteError, match="Symlink ancestor"):
        verify.file_identity(repository, "scripts/link.py")
    with pytest.raises(verify.IncompleteError):
        verify.fingerprint(repository)


def test_previous_pass_revoked_before_check_and_interruption(repository, monkeypatch):
    pass_record(repository, monkeypatch)

    def interrupt(*args):
        record = verify.read_evidence(repository)
        assert record["status"] == "INCOMPLETE"
        assert verify.status(repository) == 1
        raise KeyboardInterrupt

    monkeypatch.setattr(verify, "execute", interrupt)
    assert verify.main() == 130
    assert verify.read_evidence(repository)["status"] == "INCOMPLETE"
    assert verify.status(repository) == 1


def test_unknown_file_revokes_previous_pass_without_checks(repository, monkeypatch):
    pass_record(repository, monkeypatch)
    (repository / "unknown.bin").write_text("synthetic")
    monkeypatch.setattr(verify, "execute", lambda *args: pytest.fail("No checks allowed"))
    assert verify.main() == 1
    assert verify.read_evidence(repository)["status"] == "INCOMPLETE"
    assert verify.status(repository) == 1


def test_check_failure_records_fail_without_logs(repository, monkeypatch):
    pass_record(repository, monkeypatch)

    def fail(command, *args):
        if "pytest" in command:
            raise subprocess.CalledProcessError(5, command, output="DO_NOT_STORE_RAW_LOGS")

    monkeypatch.setattr(verify, "execute", fail)
    assert verify.main() == 5
    record = verify.read_evidence(repository)
    assert record["status"] == "FAIL"
    assert record["checks"][0]["returncode"] == 5
    assert record["checks"][1]["status"] == "NOT_RUN"
    assert "DO_NOT_STORE_RAW_LOGS" not in json.dumps(record)
    assert "original" not in json.dumps(record)
    assert verify.status(repository) == 1


def test_final_publication_failure_leaves_incomplete(repository, monkeypatch):
    pass_record(repository, monkeypatch)
    original = verify.publish
    calls = 0

    def publish(root, record):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("synthetic write failure")
        original(root, record)

    monkeypatch.setattr(verify, "publish", publish)
    assert verify.main() == 1
    assert verify.read_evidence(repository)["status"] == "INCOMPLETE"
    assert verify.status(repository) == 1


@pytest.mark.parametrize("kind", ["missing", "truncated", "schema", "unsupported_pass"])
def test_untrustworthy_record_cannot_pass(repository, monkeypatch, kind):
    record = pass_record(repository, monkeypatch)
    path = repository / ".verification" / "evidence.json"
    if kind == "missing":
        path.unlink()
    elif kind == "truncated":
        path.write_text('{"schema":')
    else:
        if kind == "schema":
            record["schema"] = 999
        else:
            record["checks"][0]["status"] = "NOT_RUN"
        verify.publish(repository, record)
    assert verify.status(repository) == 1


@pytest.mark.parametrize("kind", ["directory_symlink", "record_symlink", "tracked"])
def test_evidence_storage_refuses_unsafe_paths(repository, tmp_path, kind):
    directory = repository / ".verification"
    target = tmp_path / "external"
    target.mkdir()
    if kind == "directory_symlink":
        directory.symlink_to(target, target_is_directory=True)
    else:
        directory.mkdir()
        path = directory / "evidence.json"
        if kind == "record_symlink":
            path.symlink_to(target / "absent.json")
        else:
            path.write_text("{}")
            verify.git(repository, "add", "-f", ".verification/evidence.json")
    with pytest.raises(verify.IncompleteError):
        verify.publish(repository, {})
    assert not list(target.iterdir())


def test_head_and_policy_changes_are_stale(repository, monkeypatch, capsys):
    pass_record(repository, monkeypatch)
    original_git = verify.git

    def git(root, *args):
        if args == ("rev-parse", "HEAD"):
            return b"new-head\n"
        return original_git(root, *args)

    with monkeypatch.context() as patch:
        patch.setattr(verify, "git", git)
        assert verify.status(repository) == 1
    monkeypatch.setattr(verify, "POLICY", 2)
    assert verify.status(repository) == 1
    assert "STALE" in capsys.readouterr().out


def test_status_never_executes_checks(repository, monkeypatch):
    pass_record(repository, monkeypatch)
    monkeypatch.setattr(verify, "execute", lambda *args: pytest.fail("Status must not run checks"))
    assert verify.status(repository) == 0


def test_signaled_check_records_incomplete(repository, monkeypatch):
    pass_record(repository, monkeypatch)

    def interrupted(command, *args):
        if "pytest" in command:
            raise subprocess.CalledProcessError(-15, command)

    monkeypatch.setattr(verify, "execute", interrupted)
    assert verify.main() == 143
    record = verify.read_evidence(repository)
    assert record["status"] == "INCOMPLETE"
    assert record["checks"][0]["returncode"] == 143
    assert verify.status(repository) == 1


def test_branch_rename_alone_does_not_invalidate(repository, monkeypatch, capsys):
    pass_record(repository, monkeypatch)
    original_git = verify.git

    def git(root, *args):
        if args == ("rev-parse", "--abbrev-ref", "HEAD"):
            return b"renamed-branch\n"
        return original_git(root, *args)

    monkeypatch.setattr(verify, "git", git)
    assert verify.status(repository) == 0
    assert "Branch name changed" in capsys.readouterr().out
