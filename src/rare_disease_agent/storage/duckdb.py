"""Bounded, parameterized DuckDB queries over normalized Parquet variants."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb

from rare_disease_agent.tools.variants.filtering import VariantFilter


class VariantDataset:
    def __init__(self, parquet_path: Path | str) -> None:
        path = Path(parquet_path)
        if not path.is_file():
            raise FileNotFoundError(f"Parquet dataset does not exist: {path}")
        self._connection = duckdb.connect(":memory:")
        self._connection.from_parquet(str(path.resolve())).create_view("variants")

    def __enter__(self) -> VariantDataset:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._connection.close()

    def count(self) -> int:
        return int(self._connection.execute("SELECT count(*) FROM variants").fetchone()[0])

    def filter(self, criteria: VariantFilter, *, limit: int = 100) -> list[dict[str, Any]]:
        """Return a compact result; callers cannot inject arbitrary SQL."""

        if limit < 1 or limit > 10_000:
            raise ValueError("limit must be between 1 and 10,000")
        predicates: list[str] = []
        parameters: list[object] = []
        if criteria.max_allele_frequency is not None:
            clause = "(allele_frequency IS NULL OR allele_frequency <= ?)"
            parameters.append(criteria.max_allele_frequency)
            if criteria.preserve_pathogenic_clinvar:
                clause = f"({clause} OR lower(clinvar_classification) LIKE '%pathogenic%')"
            predicates.append(clause)
        if criteria.min_cadd_score is not None:
            predicates.append("cadd_score >= ?")
            parameters.append(criteria.min_cadd_score)
        if criteria.genes:
            placeholders = ", ".join("?" for _ in criteria.genes)
            predicates.append(f"gene IN ({placeholders})")
            parameters.extend(criteria.genes)
        if criteria.consequences:
            placeholders = ", ".join("?" for _ in criteria.consequences)
            predicates.append(f"consequence IN ({placeholders})")
            parameters.extend(criteria.consequences)
        where = f" WHERE {' AND '.join(predicates)}" if predicates else ""
        query = f"SELECT * FROM variants{where} ORDER BY chromosome, position LIMIT ?"
        parameters.append(limit)
        cursor = self._connection.execute(query, parameters)
        columns = [description[0] for description in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
