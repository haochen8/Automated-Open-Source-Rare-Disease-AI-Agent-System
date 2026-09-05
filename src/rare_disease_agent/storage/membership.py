"""Persistent DuckDB-backed candidate branch membership."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import duckdb

from rare_disease_agent.agents.schemas import CandidateSetSummary


class CandidateMembershipStore:
    """Store branch membership without materializing variant identifiers in workflow state."""

    def __init__(
        self,
        parquet_path: Path | str,
        *,
        run_id: str,
        database_path: Path | str | None = None,
    ) -> None:
        source = Path(parquet_path)
        if not source.is_file():
            raise FileNotFoundError(f"Parquet dataset does not exist: {source}")
        if database_path is not None:
            destination = Path(database_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            self.database_path: Path | None = destination
            self._connection = duckdb.connect(str(destination))
        else:
            self.database_path = None
            self._connection = duckdb.connect(":memory:")
        self.run_id = run_id
        self._connection.from_parquet(str(source.resolve())).create_view("variant_source")
        self._connection.execute(
            "CREATE TEMP TABLE variants AS "
            "SELECT row_number() OVER () AS _row_id, * FROM variant_source"
        )
        duplicate = self._connection.execute(
            "SELECT variant_id FROM variants GROUP BY variant_id HAVING count(*) > 1 LIMIT 1"
        ).fetchone()
        if duplicate:
            raise ValueError(f"variant_id must be unique; duplicate found: {duplicate[0]}")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS candidate_branches (
                run_id VARCHAR NOT NULL,
                branch VARCHAR NOT NULL,
                parent_branch VARCHAR,
                stage INTEGER NOT NULL,
                operation VARCHAR NOT NULL,
                operations_json VARCHAR NOT NULL,
                PRIMARY KEY (run_id, branch)
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS candidate_membership (
                run_id VARCHAR NOT NULL,
                variant_id VARCHAR NOT NULL,
                branch VARCHAR NOT NULL,
                stage INTEGER NOT NULL,
                active BOOLEAN NOT NULL,
                reason_code VARCHAR NOT NULL,
                PRIMARY KEY (run_id, variant_id, branch)
            )
            """
        )
        self._connection.execute("DELETE FROM candidate_membership WHERE run_id = ?", [self.run_id])
        self._connection.execute("DELETE FROM candidate_branches WHERE run_id = ?", [self.run_id])
        self._connection.execute(
            "INSERT INTO candidate_branches VALUES (?, 'all', NULL, 0, 'source', '[]')",
            [self.run_id],
        )
        self._connection.execute(
            """
            INSERT INTO candidate_membership
            SELECT ?, variant_id, 'all', 0, true, 'source'
            FROM variants
            """,
            [self.run_id],
        )

    def close(self) -> None:
        self._connection.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self._connection.execute("BEGIN TRANSACTION")
        try:
            yield
        except Exception:
            self._connection.execute("ROLLBACK")
            raise
        else:
            self._connection.execute("COMMIT")

    def _branch_metadata(self, branch: str) -> tuple[int, list[str]]:
        row = self._connection.execute(
            "SELECT stage, operations_json FROM candidate_branches WHERE run_id = ? AND branch = ?",
            [self.run_id, branch],
        ).fetchone()
        if row is None:
            raise KeyError(branch)
        return int(row[0]), list(json.loads(row[1]))

    def has_branch(self, branch: str) -> bool:
        return (
            self._connection.execute(
                "SELECT 1 FROM candidate_branches WHERE run_id = ? AND branch = ?",
                [self.run_id, branch],
            ).fetchone()
            is not None
        )

    def count(self, branch: str) -> int:
        self._branch_metadata(branch)
        return int(
            self._connection.execute(
                "SELECT count(*) FROM candidate_membership "
                "WHERE run_id = ? AND branch = ? AND active",
                [self.run_id, branch],
            ).fetchone()[0]
        )

    def candidate_ids(self, branch: str) -> list[str]:
        self._branch_metadata(branch)
        return [
            str(row[0])
            for row in self._connection.execute(
                """
                SELECT m.variant_id
                FROM candidate_membership m
                JOIN variants v USING (variant_id)
                WHERE m.run_id = ? AND m.branch = ? AND m.active
                ORDER BY v._row_id
                """,
                [self.run_id, branch],
            ).fetchall()
        ]

    def candidate_rows(self, branch: str) -> list[dict[str, Any]]:
        self._branch_metadata(branch)
        cursor = self._connection.execute(
            """
            SELECT v.* EXCLUDE (_row_id)
            FROM candidate_membership m
            JOIN variants v USING (variant_id)
            WHERE m.run_id = ? AND m.branch = ? AND m.active
            ORDER BY v._row_id
            """,
            [self.run_id, branch],
        )
        columns = [description[0] for description in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]

    def _register_branch(
        self, *, source: str | None, target: str, operation: str
    ) -> tuple[int, list[str]]:
        if self.has_branch(target):
            raise ValueError(f"Candidate branch already exists: {target}")
        if source is None:
            stage = int(
                self._connection.execute(
                    "SELECT coalesce(max(stage), 0) + 1 FROM candidate_branches WHERE run_id = ?",
                    [self.run_id],
                ).fetchone()[0]
            )
            operations: list[str] = [operation]
        else:
            parent_stage, parent_operations = self._branch_metadata(source)
            stage = parent_stage + 1
            operations = [*parent_operations, operation]
        self._connection.execute(
            "INSERT INTO candidate_branches VALUES (?, ?, ?, ?, ?, ?)",
            [self.run_id, target, source, stage, operation, json.dumps(operations)],
        )
        return stage, operations

    def copy_branch(self, *, source: str, target: str, operation: str) -> None:
        stage, _ = self._register_branch(source=source, target=target, operation=operation)
        self._connection.execute(
            """
            INSERT INTO candidate_membership
            SELECT run_id, variant_id, ?, ?, true, ?
            FROM candidate_membership
            WHERE run_id = ? AND branch = ? AND active
            """,
            [target, stage, operation, self.run_id, source],
        )

    def filter_branch(
        self,
        *,
        source: str,
        target: str,
        predicate_sql: str,
        predicate_parameters: list[object],
        operation: str,
    ) -> None:
        """Create a branch using an internal, non-user-authored predicate template."""

        stage, _ = self._register_branch(source=source, target=target, operation=operation)
        query = f"""
            INSERT INTO candidate_membership
            SELECT ?, v.variant_id, ?, ?, true, ?
            FROM variants v
            JOIN candidate_membership m USING (variant_id)
            WHERE m.run_id = ? AND m.branch = ? AND m.active AND ({predicate_sql})
        """
        self._connection.execute(
            query,
            [self.run_id, target, stage, operation, self.run_id, source, *predicate_parameters],
        )

    def branch_from_ids(
        self, *, source: str, target: str, variant_ids: list[str], operation: str
    ) -> None:
        stage, _ = self._register_branch(source=source, target=target, operation=operation)
        self._connection.execute("CREATE TEMP TABLE selected_ids (variant_id VARCHAR PRIMARY KEY)")
        try:
            if variant_ids:
                self._connection.executemany(
                    "INSERT INTO selected_ids VALUES (?)", [(value,) for value in variant_ids]
                )
            self._connection.execute(
                """
                INSERT INTO candidate_membership
                SELECT ?, m.variant_id, ?, ?, true, ?
                FROM candidate_membership m
                JOIN selected_ids s USING (variant_id)
                WHERE m.run_id = ? AND m.branch = ? AND m.active
                """,
                [self.run_id, target, stage, operation, self.run_id, source],
            )
        finally:
            self._connection.execute("DROP TABLE selected_ids")

    def union_branches(self, *, branches: list[str], target: str, operation: str) -> None:
        for branch in branches:
            self._branch_metadata(branch)
        stage, _ = self._register_branch(source=None, target=target, operation=operation)
        placeholders = ", ".join("?" for _ in branches)
        self._connection.execute(
            f"""
            INSERT INTO candidate_membership
            SELECT ?, variant_id, ?, ?, true, ?
            FROM candidate_membership
            WHERE run_id = ? AND active AND branch IN ({placeholders})
            GROUP BY variant_id
            """,
            [self.run_id, target, stage, operation, self.run_id, *branches],
        )

    def delete_branch(self, branch: str) -> None:
        if branch == "all":
            raise ValueError("The all branch cannot be deleted.")
        self._connection.execute(
            "DELETE FROM candidate_membership WHERE run_id = ? AND branch = ?",
            [self.run_id, branch],
        )
        self._connection.execute(
            "DELETE FROM candidate_branches WHERE run_id = ? AND branch = ?",
            [self.run_id, branch],
        )

    def summaries(self) -> list[CandidateSetSummary]:
        rows = self._connection.execute(
            """
            SELECT b.branch, b.parent_branch, b.operations_json, count(m.variant_id)
            FROM candidate_branches b
            LEFT JOIN candidate_membership m
              ON b.run_id = m.run_id AND b.branch = m.branch AND m.active
            WHERE b.run_id = ?
            GROUP BY b.branch, b.parent_branch, b.operations_json
            ORDER BY b.branch
            """,
            [self.run_id],
        ).fetchall()
        return [
            CandidateSetSummary(
                branch=str(branch),
                parent_branch=parent,
                operations=list(json.loads(operations)),
                count=int(count),
            )
            for branch, parent, operations, count in rows
        ]

    def write_scores(self, rows: list[dict[str, Any]]) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS variant_scores (
                run_id VARCHAR NOT NULL,
                variant_id VARCHAR NOT NULL,
                mode VARCHAR NOT NULL,
                rank INTEGER NOT NULL,
                raw_score DOUBLE NOT NULL,
                features_json VARCHAR NOT NULL,
                PRIMARY KEY (run_id, variant_id, mode)
            )
            """
        )
        values = [
            (
                self.run_id,
                row["variant_id"],
                row["mode"],
                row["rank"],
                row["raw_score"],
                json.dumps(row["features"], sort_keys=True),
            )
            for row in rows
        ]
        if values:
            self._connection.executemany(
                "INSERT OR REPLACE INTO variant_scores VALUES (?, ?, ?, ?, ?, ?)", values
            )
