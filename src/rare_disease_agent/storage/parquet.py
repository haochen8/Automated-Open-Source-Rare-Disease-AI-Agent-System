"""Streaming conversion from normalized variants to Parquet."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from rare_disease_agent.tools.variants.vcf import VariantRecord

VARIANT_SCHEMA = pa.schema(
    [
        ("chromosome", pa.string()),
        ("position", pa.int64()),
        ("reference", pa.string()),
        ("alternate", pa.string()),
        ("variant_id", pa.string()),
        ("gene", pa.string()),
        ("transcript", pa.string()),
        ("consequence", pa.string()),
        ("protein_change", pa.string()),
        ("genotype", pa.string()),
        ("quality", pa.float64()),
        ("allele_frequency", pa.float64()),
        ("clinvar_classification", pa.string()),
        ("cadd_score", pa.float64()),
        ("revel_score", pa.float64()),
        ("alphamissense_score", pa.float64()),
        ("zygosity", pa.string()),
        ("inheritance_information", pa.string()),
        ("genotype_calls_json", pa.string()),
        ("phenotype_score", pa.float64()),
        ("evidence_score", pa.float64()),
    ]
)


def write_variants_parquet(
    records: Iterable[VariantRecord], output_path: Path | str, *, batch_size: int = 10_000
) -> int:
    """Write records in bounded batches and return the number written."""

    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    writer: pq.ParquetWriter | None = None
    batch: list[dict[str, object]] = []
    count = 0
    try:
        for record in records:
            batch.append(record.model_dump())
            if len(batch) >= batch_size:
                table = pa.Table.from_pylist(batch, schema=VARIANT_SCHEMA)
                writer = writer or pq.ParquetWriter(destination, VARIANT_SCHEMA, compression="zstd")
                writer.write_table(table)
                count += len(batch)
                batch.clear()
        if batch:
            table = pa.Table.from_pylist(batch, schema=VARIANT_SCHEMA)
            writer = writer or pq.ParquetWriter(destination, VARIANT_SCHEMA, compression="zstd")
            writer.write_table(table)
            count += len(batch)
        if writer is None:
            writer = pq.ParquetWriter(destination, VARIANT_SCHEMA, compression="zstd")
    finally:
        if writer is not None:
            writer.close()
    return count
