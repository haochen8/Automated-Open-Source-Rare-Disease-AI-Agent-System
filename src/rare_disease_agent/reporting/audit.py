"""Append-only JSONL audit and atomic summary artifact writing."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from rare_disease_agent.agents.schemas import FilterDecisionRecord, FilteringMetrics


class AuditWriter:
    def __init__(self, run_directory: Path | str) -> None:
        self.run_directory = Path(run_directory)
        self.run_directory.mkdir(parents=True, exist_ok=True)
        self.audit_path = self.run_directory / "audit_log.jsonl"
        self.metrics_path = self.run_directory / "metrics.json"
        self.candidates_path = self.run_directory / "final_candidates.json"

    def _append(self, payload: dict[str, Any]) -> None:
        with self.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")

    def write_decision(self, record: FilterDecisionRecord) -> None:
        self._append({"record_type": "agent_decision", **record.model_dump(mode="json")})

    def write_event(self, event: str, *, run_id: str, data: dict[str, Any]) -> None:
        self._append(
            {
                "record_type": "workflow_event",
                "timestamp": datetime.now(UTC).isoformat(),
                "run_id": run_id,
                "event": event,
                "data": data,
            }
        )

    @staticmethod
    def _write_json(path: Path, payload: BaseModel | dict[str, Any] | list[Any]) -> None:
        value = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(path)

    def write_metrics(self, metrics: FilteringMetrics) -> None:
        self._write_json(self.metrics_path, metrics)

    def write_candidates(self, candidate_ids: list[str]) -> None:
        self._write_json(self.candidates_path, {"candidate_variant_ids": candidate_ids})
