"""Fail-closed validation for explicitly described, normalized genomic inputs."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

import duckdb
from pydantic import BaseModel, ConfigDict, Field, model_validator

from rare_disease_agent.tools.inheritance.schemas import GenotypeCall, Pedigree


class GenomicInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    genome_build: Literal["GRCh37", "GRCh38"]
    annotation_build: Literal["GRCh37", "GRCh38"]
    normalized_biallelic: Literal[True]
    reference_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    transcript_release: str = Field(min_length=1)
    sample_ids: list[str] = Field(min_length=1, max_length=1000)
    pedigree: Pedigree
    # Explicit intervals are required to distinguish pseudoautosomal ploidy.
    par_intervals: dict[str, list[tuple[int, int]]]
    allow_missing_annotations: bool = False

    @model_validator(mode="after")
    def consistent(self):
        if self.genome_build != self.annotation_build:
            raise ValueError("Genome and annotation builds differ")
        if len(set(self.sample_ids)) != len(self.sample_ids):
            raise ValueError("Duplicate sample identities")
        if set(self.sample_ids) != {item.id for item in self.pedigree.individuals}:
            raise ValueError("Samples and pedigree identities differ")
        if not {"X", "Y"} <= self.par_intervals.keys():
            raise ValueError("Explicit X/Y PAR intervals required")
        for contig, intervals in self.par_intervals.items():
            if contig not in {"X", "Y"} or any(
                start < 1 or end < start for start, end in intervals
            ):
                raise ValueError("Invalid PAR intervals")
        if self.transcript_release.lower() in {"latest", "main", "unknown"}:
            raise ValueError("Transcript release must be pinned")
        return self


class PreflightResult(BaseModel):
    schema_version: Literal[1] = 1
    records: int
    missing_genotypes: int
    missing_annotations: int
    warnings: list[str]


def validate_parquet(path: Path, specification: GenomicInput) -> PreflightResult:
    count = missing_gt = missing_annotation = 0
    with duckdb.connect() as connection:
        connection.execute("SET memory_limit='512MB'")
        connection.from_parquet(str(path)).create_view("variants")
        if connection.execute(
            "SELECT 1 FROM variants GROUP BY variant_id HAVING count(*)>1 LIMIT 1"
        ).fetchone():
            raise ValueError("Duplicate variant IDs")
        cursor = connection.execute("SELECT * FROM variants")
        columns = [item[0] for item in cursor.description]
        while batch := cursor.fetchmany(256):
            for values in batch:
                row = dict(zip(columns, values, strict=True))
                count += 1
                contig = str(row.get("chromosome", "")).removeprefix("chr")
                if contig not in {str(i) for i in range(1, 23)} | {"X", "Y", "MT"}:
                    raise ValueError("Unsupported or ambiguous contig")
                position = row.get("position")
                if not isinstance(position, int) or position <= 0 or not row.get("variant_id"):
                    raise ValueError("Invalid variant identity or position")
                for field in ("reference", "alternate"):
                    # The normalized schema uses reference_allele/alternate_allele.
                    value = row.get(field + "_allele", row.get(field))
                    if not value or not re.fullmatch("[ACGT]+", value.upper()):
                        raise ValueError("Unnormalized, symbolic or multiallelic record")
                for field in ("gene", "transcript", "consequence", "allele_frequency"):
                    if row.get(field) is None or row.get(field) == "":
                        missing_annotation += 1
                        if not specification.allow_missing_annotations:
                            raise ValueError("Required annotation missing")
                calls = json.loads(row.get("genotype_calls_json") or "{}")
                if set(calls) != set(specification.sample_ids):
                    raise ValueError("Genotype sample identities differ")
                if row.get("genotype") != calls[specification.pedigree.proband_id].get("genotype"):
                    raise ValueError("Proband genotype fields disagree")
                for sample, raw in calls.items():
                    call = GenotypeCall(
                        individual_id=sample,
                        genotype=raw.get("genotype"),
                        quality=raw.get("quality"),
                        alternate_fraction=raw.get("alternate_fraction"),
                    )
                    if call.quality is None:
                        raise ValueError("GQ field missing")
                    if any(allele not in {"0", "1", "."} for allele in call.alleles):
                        raise ValueError("Genotype not normalized to biallelic alleles")
                    sex = specification.pedigree.individual(sample).sex
                    if contig in {"X", "Y"} and sex == "unknown":
                        raise ValueError("Sex-chromosome ploidy is ambiguous")
                    par = any(
                        start <= position <= end
                        for start, end in specification.par_intervals.get(contig, [])
                    )
                    expected = (
                        1
                        if contig == "MT" or (contig in {"X", "Y"} and sex == "male" and not par)
                        else 2
                    )
                    if contig == "Y" and sex == "female" and call.called:
                        raise ValueError("Unexpected called Y genotype for female sample")
                    if not call.called:
                        missing_gt += 1
                    elif len(call.alleles) != expected:
                        raise ValueError("Genotype ploidy mismatch")
    if not count:
        raise ValueError("Empty variant input")
    warnings = []
    if missing_gt:
        warnings.append("Missing genotypes remain uncertain; no calls were imputed.")
    if missing_annotation:
        warnings.append(
            "Explicit annotation-missingness research mode; missing evidence remains uncertain."
        )
    return PreflightResult(
        records=count,
        missing_genotypes=missing_gt,
        missing_annotations=missing_annotation,
        warnings=warnings,
    )
