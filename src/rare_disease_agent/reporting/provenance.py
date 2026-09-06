"""Reproducible software and evidence provenance."""

from __future__ import annotations

import platform
import subprocess
from importlib.metadata import distributions
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SoftwareIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    git_commit: str
    git_dirty: bool
    python_version: str
    dependencies: dict[str, str]


class EvidenceProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_version: str
    data_version: str
    method: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    git_commit: str
    git_dirty: bool
    run_id: str


class RunProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    software: SoftwareIdentity
    hpo_data_version: str
    hpo_source: str
    hpo_checksum: str


def software_identity(repository: Path | str = Path(".")) -> SoftwareIdentity:
    root = Path(repository)

    def git(*arguments: str) -> str:
        try:
            completed = subprocess.run(
                ["git", *arguments],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return "unknown"
        return completed.stdout.strip()

    commit = git("rev-parse", "HEAD") or "unknown"
    status = git("status", "--porcelain")
    dependencies = dict(
        sorted(
            (distribution.metadata["Name"], distribution.version)
            for distribution in distributions()
            if distribution.metadata["Name"]
        )
    )
    return SoftwareIdentity(
        git_commit=commit,
        git_dirty=status not in {"", "unknown"},
        python_version=platform.python_version(),
        dependencies=dependencies,
    )


def evidence_provenance(
    *,
    run_id: str,
    tool_version: str,
    data_version: str,
    method: str,
    parameters: dict[str, Any] | None = None,
    software: SoftwareIdentity | None = None,
) -> EvidenceProvenance:
    identity = software or software_identity()
    return EvidenceProvenance(
        tool_version=tool_version,
        data_version=data_version,
        method=method,
        parameters=parameters or {},
        git_commit=identity.git_commit,
        git_dirty=identity.git_dirty,
        run_id=run_id,
    )
