"""Small, deterministic VCF reader for the Phase 1 normalized schema.

This intentionally handles already-normalized, single-sample VCF inputs. Production
annotation remains the responsibility of established tools such as bcftools and VEP.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Iterator
from pathlib import Path
from typing import TextIO

from pydantic import BaseModel, ConfigDict, Field


class VCFParseError(ValueError):
    pass


class VariantRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    chromosome: str
    position: int = Field(gt=0)
    reference: str
    alternate: str
    variant_id: str
    gene: str | None = None
    transcript: str | None = None
    consequence: str | None = None
    protein_change: str | None = None
    genotype: str | None = None
    quality: float | None = None
    allele_frequency: float | None = Field(default=None, ge=0, le=1)
    clinvar_classification: str | None = None
    cadd_score: float | None = None
    revel_score: float | None = Field(default=None, ge=0, le=1)
    alphamissense_score: float | None = Field(default=None, ge=0, le=1)
    zygosity: str | None = None
    inheritance_information: str | None = None
    genotype_calls_json: str | None = None
    phenotype_score: float | None = Field(default=None, ge=0, le=1)
    evidence_score: float | None = Field(default=None, ge=0, le=1)


def _open_text(path: Path) -> TextIO:
    if path.name.lower().endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8")
    return path.open("rt", encoding="utf-8")


def _optional_float(value: str | None) -> float | None:
    if value in {None, "", "."}:
        return None
    try:
        return float(value)
    except ValueError as exc:
        raise VCFParseError(f"Expected numeric annotation, got {value!r}") from exc


def _parse_info(raw: str) -> dict[str, str]:
    if raw in {"", "."}:
        return {}
    info: dict[str, str] = {}
    for item in raw.split(";"):
        key, separator, value = item.partition("=")
        info[key] = value if separator else "true"
    return info


def _allele_value(
    info: dict[str, str], key: str, *, alternate_index: int, alternate_count: int
) -> str | None:
    """Select a Number=A value while leaving scalar annotations unchanged."""

    raw = info.get(key)
    if raw is None:
        return None
    values = raw.split(",")
    if len(values) == alternate_count:
        return values[alternate_index]
    return raw


def _genotype(format_value: str | None, sample_value: str | None) -> str | None:
    if not format_value or not sample_value:
        return None
    keys = format_value.split(":")
    values = sample_value.split(":")
    try:
        return values[keys.index("GT")]
    except (ValueError, IndexError):
        return None


def _genotype_call(format_value: str, sample_value: str) -> dict[str, object]:
    keys = format_value.split(":")
    values = sample_value.split(":")
    fields = dict(zip(keys, values, strict=False))
    quality_raw = fields.get("GQ")
    try:
        quality = float(quality_raw) if quality_raw not in {None, "", "."} else None
    except ValueError as exc:
        raise VCFParseError(f"Expected numeric genotype quality, got {quality_raw!r}") from exc
    result = {"genotype": fields.get("GT"), "quality": quality}
    if fields.get("VAF") not in {None, "", "."}:
        fraction = float(fields["VAF"])
        if not 0 <= fraction <= 1:
            raise VCFParseError("Invalid alternate allele fraction")
        result["alternate_fraction"] = fraction
    return result


def _zygosity(genotype: str | None) -> str | None:
    if not genotype or genotype in {".", "./.", ".|."}:
        return None
    alleles = genotype.replace("|", "/").split("/")
    if len(alleles) == 1:
        return "hemizygous" if alleles[0] != "0" else "reference"
    if all(allele == "0" for allele in alleles):
        return "reference"
    if len(set(alleles)) == 1 and alleles[0] != "0":
        return "homozygous_alternate"
    if "0" in alleles:
        return "heterozygous"
    return "compound_alternate"


def _record_from_columns(
    columns: list[str], *, line_number: int, sample_names: list[str]
) -> list[VariantRecord]:
    if len(columns) < 8:
        raise VCFParseError(f"Line {line_number}: expected at least 8 VCF columns")
    chromosome, position_raw, identifier, reference, alternates, quality_raw, _, info_raw = columns[
        :8
    ]
    try:
        position = int(position_raw)
    except ValueError as exc:
        raise VCFParseError(f"Line {line_number}: invalid position {position_raw!r}") from exc
    info = _parse_info(info_raw)
    genotype = _genotype(
        columns[8] if len(columns) > 8 else None,
        columns[9] if len(columns) > 9 else None,
    )
    genotype_calls = {
        sample: _genotype_call(columns[8], value)
        for sample, value in zip(sample_names, columns[9:], strict=False)
    }
    records: list[VariantRecord] = []
    alternate_values = alternates.split(",")
    for alternate_index, alternate in enumerate(alternate_values):
        variant_id = (
            f"{identifier}:{alternate}"
            if identifier not in {"", "."} and len(alternate_values) > 1
            else identifier
            if identifier not in {"", "."}
            else f"{chromosome}-{position}-{reference}-{alternate}"
        )
        records.append(
            VariantRecord(
                chromosome=chromosome.removeprefix("chr"),
                position=position,
                reference=reference,
                alternate=alternate,
                variant_id=variant_id,
                gene=info.get("GENE"),
                transcript=info.get("TRANSCRIPT"),
                consequence=info.get("CONSEQUENCE") or info.get("CSQ"),
                protein_change=info.get("PROTEIN_CHANGE") or info.get("HGVSP"),
                genotype=genotype,
                quality=_optional_float(quality_raw),
                allele_frequency=_optional_float(
                    _allele_value(
                        info,
                        "AF",
                        alternate_index=alternate_index,
                        alternate_count=len(alternate_values),
                    )
                ),
                clinvar_classification=info.get("CLNSIG"),
                cadd_score=_optional_float(
                    _allele_value(
                        info,
                        "CADD",
                        alternate_index=alternate_index,
                        alternate_count=len(alternate_values),
                    )
                ),
                revel_score=_optional_float(
                    _allele_value(
                        info,
                        "REVEL",
                        alternate_index=alternate_index,
                        alternate_count=len(alternate_values),
                    )
                ),
                alphamissense_score=_optional_float(
                    _allele_value(
                        info,
                        "ALPHAMISSENSE",
                        alternate_index=alternate_index,
                        alternate_count=len(alternate_values),
                    )
                ),
                zygosity=_zygosity(genotype),
                inheritance_information=info.get("INHERITANCE"),
                genotype_calls_json=(
                    json.dumps(genotype_calls, sort_keys=True, separators=(",", ":"))
                    if genotype_calls
                    else None
                ),
                phenotype_score=_optional_float(info.get("PHENOTYPE_SCORE")),
                evidence_score=_optional_float(info.get("EVIDENCE_SCORE")),
            )
        )
    return records


def parse_vcf(path: Path | str) -> Iterator[VariantRecord]:
    """Yield normalized records without loading the complete VCF into memory."""

    input_path = Path(path)
    if not input_path.is_file():
        raise FileNotFoundError(f"VCF does not exist: {input_path}")
    saw_fileformat = False
    saw_header = False
    sample_names: list[str] = []
    with _open_text(input_path) as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.rstrip("\r\n")
            if not line:
                continue
            if line.startswith("##fileformat=VCF"):
                saw_fileformat = True
                continue
            if line.startswith("##"):
                continue
            if line.startswith("#CHROM"):
                saw_header = True
                header_columns = line.split("\t")
                sample_names = header_columns[9:] if len(header_columns) > 9 else []
                if len(set(sample_names)) != len(sample_names):
                    raise VCFParseError("Duplicate sample identities in VCF header")
                continue
            if line.startswith("#"):
                continue
            if not saw_header:
                raise VCFParseError(f"Line {line_number}: variant encountered before #CHROM header")
            yield from _record_from_columns(
                line.split("\t"), line_number=line_number, sample_names=sample_names
            )
    if not saw_fileformat:
        raise VCFParseError("Missing ##fileformat=VCF header")
    if not saw_header:
        raise VCFParseError("Missing #CHROM header")
