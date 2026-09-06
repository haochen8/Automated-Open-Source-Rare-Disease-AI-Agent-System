"""Versioned evidence with bounded ingestion, indexed lookup and streamed export."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path

import duckdb
import pyarrow as pa

from rare_disease_agent.tools.inheritance.schemas import InheritanceEvidence
from rare_disease_agent.tools.phenotype.schemas import PhenotypeScore


class EvidenceStore:
    def __init__(self, connection: duckdb.DuckDBPyConnection, run_id: str):
        self.connection = connection
        self.run_id = run_id
        connection.execute("""CREATE TABLE IF NOT EXISTS evidence (
            run_id VARCHAR, variant_id VARCHAR, gene VARCHAR, method VARCHAR,
            data_version VARCHAR, score DOUBLE, known BOOLEAN, payload VARCHAR,
            PRIMARY KEY(run_id, variant_id, gene, method, data_version))""")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS evidence_gene ON evidence(run_id, gene, method)"
        )
        self.max_batch_observed = 0

    def _write(
        self, items: Iterable[PhenotypeScore | InheritanceEvidence], *, batch_size: int = 256
    ) -> int:
        if not 1 <= batch_size <= 4096:
            raise ValueError("Evidence batch size outside bounds")
        count = 0
        batch = []
        for item in items:
            if item.provenance.run_id != self.run_id:
                raise ValueError("Mismatched evidence run ID")
            phenotype = isinstance(item, PhenotypeScore)
            batch.append(
                {
                    "run_id": self.run_id,
                    "variant_id": "" if phenotype else item.variant_id,
                    "gene": item.gene.upper(),
                    "method": item.method if phenotype else item.model,
                    "data_version": item.provenance.data_version,
                    "score": item.score if phenotype else item.fit,
                    "known": item.association_count > 0 if phenotype else True,
                    "payload": item.model_dump_json(),
                }
            )
            if len(batch) >= batch_size:
                self._flush(batch)
                count += len(batch)
                batch.clear()
        if batch:
            self._flush(batch)
            count += len(batch)
        return count

    def write(self, items, *, batch_size: int = 256) -> int:
        """Publish a whole typed operation atomically, including failed generators."""
        self.connection.execute("BEGIN TRANSACTION")
        try:
            count = self._write(items, batch_size=batch_size)
            self.connection.execute("COMMIT")
            return count
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise

    def _flush(self, batch: list[dict]) -> None:
        versions = {}
        for item in batch:
            previous = versions.setdefault(item["method"], item["data_version"])
            if previous != item["data_version"]:
                raise ValueError("Mixed evidence data versions")
        for method, version in versions.items():
            previous = self.connection.execute(
                "SELECT DISTINCT data_version FROM evidence WHERE run_id=? AND method=?",
                [self.run_id, method],
            ).fetchall()
            if previous and previous != [(version,)]:
                raise ValueError("Stale evidence data version; start a new run")
        self.max_batch_observed = max(self.max_batch_observed, len(batch))
        self.connection.register("incoming_evidence", pa.Table.from_pylist(batch))
        try:
            self.connection.execute("""INSERT INTO evidence SELECT * FROM incoming_evidence
                ON CONFLICT (run_id, variant_id, gene, method, data_version)
                DO UPDATE SET score=excluded.score, known=excluded.known,
payload=excluded.payload
                WHERE excluded.score >= evidence.score""")
        finally:
            self.connection.unregister("incoming_evidence")

    def payloads(self, *, phenotype: bool, variant_id: str | None = None) -> Iterator[dict]:
        cursor = self.connection.cursor()
        try:
            query = "SELECT payload FROM evidence WHERE run_id=? AND (method='resnik_bma')=?"
            params = [self.run_id, phenotype]
            if variant_id is not None:
                query += " AND variant_id=?"
                params.append(variant_id)
            cursor.execute(query + " ORDER BY gene, variant_id, method", params)
            while batch := cursor.fetchmany(256):
                for row in batch:
                    yield json.loads(row[0])
        finally:
            cursor.close()

    def export(self, path: Path) -> None:
        self.connection.sql(
            "SELECT * FROM evidence WHERE run_id=$run",
            params={"run": self.run_id},
        ).write_parquet(str(path))

    def score_mapping(self, phenotype: bool) -> Mapping[str, float]:
        return ScoreLookup(self, phenotype)


class ScoreLookup(Mapping[str, float]):
    """Compatibility lookup; never caches complete score dictionaries."""

    def __init__(self, store: EvidenceStore, phenotype: bool):
        self.store, self.phenotype = store, phenotype

    def __getitem__(self, key: str) -> float:
        column = "gene" if self.phenotype else "variant_id"
        row = self.store.connection.execute(
            "SELECT max(score) FROM evidence "
            f"WHERE run_id=? AND (method='resnik_bma')=? AND {column}=?",
            [self.store.run_id, self.phenotype, key],
        ).fetchone()
        if row[0] is None:
            raise KeyError(key)
        return row[0]

    def __iter__(self):
        column = "gene" if self.phenotype else "variant_id"
        cursor = self.store.connection.cursor()
        try:
            cursor.execute(
                f"SELECT DISTINCT {column} FROM evidence "
                "WHERE run_id=? AND (method='resnik_bma')=?",
                [self.store.run_id, self.phenotype],
            )
            while batch := cursor.fetchmany(256):
                yield from (row[0] for row in batch)
        finally:
            cursor.close()

    def __len__(self):
        column = "gene" if self.phenotype else "variant_id"
        return self.store.connection.execute(
            f"SELECT count(DISTINCT {column}) FROM evidence "
            "WHERE run_id=? AND (method='resnik_bma')=?",
            [self.store.run_id, self.phenotype],
        ).fetchone()[0]
