"""Atomic stage publication with immutable logical audit events and integrity checks."""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import psutil
from pydantic import BaseModel, ConfigDict, Field

from rare_disease_agent.reporting.provenance import software_identity
from rare_disease_agent.resource_management import atomic_json, sha256


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class StageRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ordinal: int
    outputs: dict[str, str]
    started_at: str
    ended_at: str


class RunJournal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    run_id: str
    configuration_hash: str
    configuration: dict
    provenance: dict
    started_at: str
    ended_at: str | None = None
    failure_reason: str | None = None
    stages: dict[str, StageRecord] = Field(default_factory=dict)


class RestartableRun:
    """Single-writer stage transaction. Incomplete stage work is recomputed on resume."""

    def __init__(self, directory: Path, *, run_id: str, configuration: dict):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "run.json"
        digest = hashlib.sha256(json.dumps(configuration, sort_keys=True).encode()).hexdigest()
        if self.path.exists():
            self.journal = RunJournal.model_validate_json(self.path.read_text())
            if self.journal.run_id != run_id or self.journal.configuration_hash != digest:
                raise ValueError("Run ID or configuration/resource identity mismatch")
            self.verify()
        else:
            if any(path.name != ".run.lock" for path in directory.iterdir()):
                raise ValueError("Refusing to adopt a nonempty directory without a run journal")
            self.journal = RunJournal(
                run_id=run_id,
                configuration_hash=digest,
                configuration=configuration,
                started_at=utc_now(),
                provenance={
                    "software": software_identity().model_dump(),
                    "host": {
                        "total_memory": psutil.virtual_memory().total,
                        "available_memory": psutil.virtual_memory().available,
                        "free_disk": psutil.disk_usage(directory).free,
                    },
                    "tool_versions": {
                        "evidence": "1",
                        "critic": "1",
                        "ranker": "preliminary-ranking-v1",
                    },
                },
            )
            self.save()

    def save(self):
        atomic_json(self.path, self.journal.model_dump(mode="json"))

    def verify(self):
        for name, stage in self.journal.stages.items():
            if not name.isidentifier():
                raise ValueError("Invalid stage identity")
            base = self.directory / name
            observed = {
                str(path.relative_to(base)): sha256(path)
                for path in base.rglob("*")
                if path.is_file()
            }
            if observed != stage.outputs:
                raise ValueError("Partial or modified stage artifacts")

    def stage(self, name: str, operation: Callable[[Path], None]) -> Path:
        if not name.isidentifier():
            raise ValueError("Invalid stage name")
        if name in self.journal.stages:
            self.verify()
            self.project_audit()
            return self.directory / name
        target = self.directory / name
        temporary = self.directory / (name + ".partial")
        # An unrecorded publication is not committed; only this stage-owned directory is replaced.
        if target.exists():
            shutil.rmtree(target)
        if temporary.exists():
            shutil.rmtree(temporary)
        temporary.mkdir()
        started = utc_now()
        try:
            operation(temporary)
            outputs = {
                str(path.relative_to(temporary)): sha256(path)
                for path in temporary.rglob("*")
                if path.is_file()
            }
            if not outputs:
                raise ValueError("Stage produced no artifacts")
            temporary.rename(target)
            self.journal.stages[name] = StageRecord(
                ordinal=len(self.journal.stages),
                outputs=outputs,
                started_at=started,
                ended_at=utc_now(),
            )
            self.journal.failure_reason = None
            self.save()
            self.project_audit()
            return target
        except BaseException as exc:
            # Exception text may contain patient paths or rows. Store only the exception class.
            self.journal.failure_reason = type(exc).__name__
            self.save()
            raise

    def project_audit(self):
        # The journal is authoritative. Projection is reconstructible after interruption.
        records = [
            {
                "event_id": f"{self.journal.run_id}:{name}",
                "run_id": self.journal.run_id,
                "event": "stage_completed",
                "stage": name,
                "ended_at": stage.ended_at,
            }
            for name, stage in sorted(self.journal.stages.items(), key=lambda item: item[1].ordinal)
        ]
        target = self.directory / "stage_audit.jsonl"
        expected = "".join(json.dumps(record, sort_keys=True) + "\n" for record in records)
        if target.exists() and not expected.startswith(target.read_text()):
            raise ValueError("Audit projection differs from immutable events")
        temporary = target.with_suffix(".partial")
        temporary.write_text(expected)
        temporary.replace(target)

    def complete(self):
        self.verify()
        self.project_audit()
        if self.journal.ended_at is None:
            self.journal.ended_at = utc_now()
            self.save()
