import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from rare_disease_agent.reporting import provenance
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


def synthetic_repository(path):
    path.mkdir()
    environment = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=path, env=environment, check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init")
    (path / "synthetic.txt").write_text("Deliberately synthetic provenance fixture.\n")
    git("add", "synthetic.txt")
    git(
        "-c",
        "user.name=Synthetic Test",
        "-c",
        "user.email=synthetic@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "-c",
        "core.hooksPath=/dev/null",
        "commit",
        "-m",
        "synthetic provenance",
    )
    return git("rev-parse", "HEAD")


def test_worker_journal_and_evidence_identify_imported_checkout_from_foreign_cwd(tmp_path):
    expected = software_identity()
    assert expected.git_commit != "unknown"
    foreign = tmp_path / "unrelated-repository"
    foreign_commit = synthetic_repository(foreign)
    assert foreign_commit != expected.git_commit
    worker = foreign / "stage"
    worker.mkdir()
    environment = dict(os.environ)
    environment.update(
        PYTHONPATH=str(Path(provenance.__file__).resolve().parents[2]),
        GIT_DIR=str(foreign / ".git"),
        GIT_WORK_TREE=str(foreign),
        LANGSMITH_TRACING="false",
        LANGCHAIN_TRACING_V2="false",
    )
    code = """
import json
from pathlib import Path
from rare_disease_agent.reporting.provenance import evidence_provenance, software_identity
from rare_disease_agent.workflows.recovery import RestartableRun
identity = software_identity()
journal = RestartableRun(Path('run'), run_id='synthetic-worker', configuration={'synthetic': True})
evidence = evidence_provenance(run_id='synthetic-worker', tool_version='synthetic-v1',
    data_version='synthetic-v1', method='synthetic')
print(json.dumps({'identity': identity.model_dump(),
    'journal': journal.journal.provenance['software'],
    'evidence': evidence.model_dump()}))
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=worker,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    observed = json.loads(result.stdout)
    assert observed["identity"] == expected.model_dump()
    assert observed["journal"] == expected.model_dump()
    assert observed["evidence"]["git_commit"] == expected.git_commit
    assert observed["evidence"]["git_dirty"] == expected.git_dirty


def test_explicit_repository_preserves_selection_and_dirty_state(tmp_path, monkeypatch):
    repository = tmp_path / "selected"
    commit = synthetic_repository(repository)
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "nonexistent"))
    clean = software_identity(repository)
    assert clean.git_commit == commit and clean.git_dirty is False
    (repository / "synthetic.txt").write_text("Modified synthetic fixture.\n")
    dirty = software_identity(repository)
    assert dirty.git_commit == commit and dirty.git_dirty is True


@pytest.mark.parametrize("layout", ["site-packages", "src"])
def test_package_without_source_checkout_does_not_borrow_cwd_or_parent_git(
    tmp_path, monkeypatch, layout
):
    foreign = tmp_path / "unrelated"
    synthetic_repository(foreign)
    module = foreign / "nested" / layout / "rare_disease_agent/reporting/provenance.py"
    module.parent.mkdir(parents=True)
    module.write_text("# Synthetic package location without its own checkout\n")
    monkeypatch.setattr(provenance, "__file__", str(module))
    monkeypatch.chdir(foreign)
    identity = software_identity()
    assert identity.git_commit == "unknown"
    assert identity.python_version and "pydantic" in identity.dependencies


def test_unavailable_git_remains_unknown_without_exposing_error(monkeypatch):
    def unavailable(*args, **kwargs):
        raise OSError("synthetic unavailable executable")

    monkeypatch.setattr(provenance.subprocess, "run", unavailable)
    identity = software_identity()
    assert identity.git_commit == "unknown"
