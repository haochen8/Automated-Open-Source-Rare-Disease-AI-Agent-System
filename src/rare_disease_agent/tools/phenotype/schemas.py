"""Typed phenotype inputs, outputs, and dataset metadata."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from rare_disease_agent.reporting.provenance import EvidenceProvenance

HPO_ID_PATTERN = r"^HP:\d{7}$"


class HPOInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hpo_terms: list[str] = Field(min_length=1, max_length=500)


class HPOTerm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hpo_id: str = Field(pattern=HPO_ID_PATTERN)
    label: str
    parents: list[str] = Field(default_factory=list)
    obsolete: bool = False
    replaced_by: str | None = Field(default=None, pattern=HPO_ID_PATTERN)

    @field_validator("parents")
    @classmethod
    def validate_parents(cls, values: list[str]) -> list[str]:
        for value in values:
            if not re.fullmatch(HPO_ID_PATTERN, value):
                raise ValueError(f"Invalid parent HPO identifier: {value}")
        return values


class NormalizedPhenotypes(BaseModel):
    model_config = ConfigDict(extra="forbid")

    terms: list[HPOTerm]
    invalid_ids: list[str] = Field(default_factory=list)
    unknown_ids: list[str] = Field(default_factory=list)
    replacements: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class PhenotypeDatasetProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    release: str
    downloaded_at: datetime
    checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    license_reference: str
    synthetic: bool = False


class GeneAssociation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gene: str
    hpo_id: str = Field(pattern=HPO_ID_PATTERN)
    disease_id: str | None = None
    evidence: str | None = None


class PhenotypeScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gene: str
    score: float = Field(ge=0, le=1)
    matched_terms: list[str]
    unmatched_patient_terms: list[str]
    method: Literal["resnik_bma"] = "resnik_bma"
    data_version: str
    association_count: int = Field(ge=0)
    provenance: EvidenceProvenance


class GenePhenotypeSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gene: str
    score: float = Field(ge=0, le=1)
    matched_terms: list[str] = Field(default_factory=list)
    association_known: bool


class GeneAssociationsResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gene: str
    associations: list[GeneAssociation]
    data_version: str
    provenance: EvidenceProvenance


class DiseaseAssociationsResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    disease_id: str
    associations: list[GeneAssociation]
    data_version: str
    provenance: EvidenceProvenance


class PatientHPOSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    normalized: NormalizedPhenotypes
    term_count: int = Field(ge=0)
    data_version: str
    provenance: EvidenceProvenance
