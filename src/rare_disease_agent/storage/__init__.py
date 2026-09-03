"""Local analytical storage primitives."""

from rare_disease_agent.storage.duckdb import VariantDataset
from rare_disease_agent.storage.parquet import write_variants_parquet

__all__ = ["VariantDataset", "write_variants_parquet"]
