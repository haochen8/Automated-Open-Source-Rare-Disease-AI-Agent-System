"""Typed pedigree, genotype, and inheritance evidence schemas."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from rare_disease_agent.reporting.provenance import EvidenceProvenance

InheritanceModel = Literal[
    "autosomal_dominant",
    "autosomal_recessive",
    "homozygous_recessive",
    "de_novo",
    "x_linked",
    "compound_heterozygous",
]


class Individual(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
    sex: Literal["female", "male", "unknown"] = "unknown"
    affected: bool | None = None
    mother_id: str | None = None
    father_id: str | None = None


class Pedigree(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    proband_id: str
    mother_id: str | None = None
    father_id: str | None = None
    individuals: list[Individual] = Field(min_length=1, max_length=1_000)

    @model_validator(mode="after")
    def validate_relationships(self) -> Pedigree:
        ids = [individual.id for individual in self.individuals]
        if len(ids) != len(set(ids)):
            raise ValueError("Pedigree individual identifiers must be unique")
        known = set(ids)
        if self.proband_id not in known:
            raise ValueError("proband_id must identify an individual")
        for parent in (self.mother_id, self.father_id):
            if parent is not None and parent not in known:
                raise ValueError("Pedigree parent identifiers must identify individuals")
            if parent == self.proband_id:
                raise ValueError("The proband cannot be their own parent")
        for individual in self.individuals:
            for parent in (individual.mother_id, individual.father_id):
                if parent is not None and parent not in known:
                    raise ValueError("Individual parent identifiers must identify individuals")
                if parent == individual.id:
                    raise ValueError("An individual cannot be their own parent")
        if self.mother_id is not None and self.mother_id == self.father_id:
            raise ValueError("Parents must be distinct")
        edges = {
            person.id: [p for p in (person.mother_id, person.father_id) if p]
            for person in self.individuals
        }
        edges[self.proband_id] = list(
            set(edges[self.proband_id] + [p for p in (self.mother_id, self.father_id) if p])
        )

        colors = {}
        for root in edges:
            pending = [(root, False)]
            while pending:
                node, leaving = pending.pop()
                if leaving:
                    colors[node] = 2
                    continue
                if colors.get(node) == 2:
                    continue
                if colors.get(node) == 1:
                    raise ValueError("Pedigree ancestry cycle")
                colors[node] = 1
                pending.append((node, True))
                pending.extend((parent, False) for parent in edges[node])
        return self

    def individual(self, individual_id: str) -> Individual | None:
        return next((item for item in self.individuals if item.id == individual_id), None)


class GenotypeCall(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    individual_id: str
    genotype: str | None = None
    quality: float | None = Field(default=None, ge=0)
    alternate_fraction: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_genotype(self) -> GenotypeCall:
        if self.genotype is not None and not re.fullmatch(
            r"(?:[0-9.]+)(?:[/|][0-9.]+)?", self.genotype
        ):
            raise ValueError(f"Unsupported genotype: {self.genotype}")
        return self

    @property
    def alleles(self) -> list[str]:
        if self.genotype in {None, ".", "./.", ".|."}:
            return []
        return re.split(r"[/|]", self.genotype)

    @property
    def called(self) -> bool:
        return bool(self.alleles) and "." not in self.alleles

    @property
    def has_alternate(self) -> bool:
        return self.called and any(allele != "0" for allele in self.alleles)

    @property
    def is_reference(self) -> bool:
        return self.called and all(allele == "0" for allele in self.alleles)

    @property
    def is_heterozygous(self) -> bool:
        return self.called and len(self.alleles) == 2 and len(set(self.alleles)) == 2

    @property
    def is_homozygous_alternate(self) -> bool:
        return (
            self.called
            and len(self.alleles) == 2
            and len(set(self.alleles)) == 1
            and self.alleles[0] != "0"
        )


class VariantGenotypes(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    variant_id: str
    gene: str
    chromosome: str
    calls: list[GenotypeCall]

    @model_validator(mode="after")
    def calls_must_be_unique(self) -> VariantGenotypes:
        identifiers = [call.individual_id for call in self.calls]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Variant genotype calls must have unique individual identifiers")
        return self

    def call(self, individual_id: str | None) -> GenotypeCall | None:
        if individual_id is None:
            return None
        return next((call for call in self.calls if call.individual_id == individual_id), None)


class InheritanceEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    variant_id: str
    gene: str
    model: InheritanceModel
    fit: float = Field(ge=0, le=1)
    proband_genotype: str | None
    mother_genotype: str | None
    father_genotype: str | None
    quality_checks_passed: bool
    evidence: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    provenance: EvidenceProvenance


class CompoundHeterozygousPair(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    gene: str
    variant_a: str
    variant_b: str
    phase: Literal["confirmed_trans", "possible_trans", "unknown"]
    confidence: float = Field(ge=0, le=1)
    evidence: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    provenance: EvidenceProvenance


class InheritanceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    model: InheritanceModel
    strong_matches: int = Field(ge=0)
    uncertain_matches: int = Field(ge=0)
    top_variant_ids: list[str] = Field(default_factory=list, max_length=10)


class InheritanceEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    evidence: list[InheritanceEvidence]
    compound_heterozygous_pairs: list[CompoundHeterozygousPair]
    summaries: list[InheritanceSummary]
