"""Validated inputs for deterministic DuckDB filters."""

from __future__ import annotations

from pydantic import BaseModel, Field


class VariantFilter(BaseModel):
    max_allele_frequency: float | None = Field(default=None, ge=0, le=1)
    min_cadd_score: float | None = Field(default=None, ge=0)
    genes: list[str] = Field(default_factory=list)
    consequences: list[str] = Field(default_factory=list)
    preserve_pathogenic_clinvar: bool = True
