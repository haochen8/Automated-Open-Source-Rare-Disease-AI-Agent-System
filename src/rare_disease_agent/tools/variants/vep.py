"""Strict, bounded decoding of VEP consequences for the future real-data importer.

This module neither normalizes variants nor chooses a gene/transcript for ranking.
Every CSQ entry is retained. Caller INFO/AF is deliberately outside this interface.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

POPULATION_FIELDS = ("gnomADe_AF", "gnomADg_AF")
MAX_CSQ_BYTES = 2 * 1024 * 1024
MAX_CONSEQUENCES = 5000
STANDARD_CONTIGS = frozenset({str(i) for i in range(1, 23)} | {"X", "Y", "MT"})


@dataclass(frozen=True, slots=True)
class Consequence:
    allele_number: int
    fields: tuple[tuple[str, str], ...]

    def value(self, key: str) -> str | None:
        return next((value for name, value in self.fields if name == key and value), None)


@dataclass(frozen=True, slots=True)
class VEPLayout:
    fields: tuple[str, ...]

    def __post_init__(self):
        required = {"Allele", "ALLELE_NUM", "Consequence", "Gene", "Feature", "SYMBOL"}
        if (
            not required <= set(self.fields)
            or len(self.fields) != len(set(self.fields))
            or not set(POPULATION_FIELDS) & set(self.fields)
            or any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", f) for f in self.fields)
        ):
            raise ValueError("VEP schema lacks required unambiguous annotation fields")

    @classmethod
    def from_header(cls, header: str) -> VEPLayout:
        if len(header) > 65536 or not header.startswith("##INFO=<ID=CSQ,"):
            raise ValueError("Expected a bounded CSQ header")
        match = re.search(r'Format: ([A-Za-z0-9_|]+)"', header)
        if match is None:
            raise ValueError("CSQ header does not declare its field order")
        return cls(tuple(match[1].split("|")))

    def decode(self, raw: str, *, alternate_count: int) -> tuple[Consequence, ...]:
        if not 1 <= alternate_count <= 100:
            raise ValueError("Alternate count outside supported bounds")
        if len(raw) > MAX_CSQ_BYTES or len(raw.encode()) > MAX_CSQ_BYTES:
            raise ValueError("CSQ payload exceeds bounded work budget")
        if raw in {"", "."}:
            return ()
        if raw.count(",") >= MAX_CONSEQUENCES:
            raise ValueError("CSQ consequence count exceeds bounded work budget")
        output = []
        for entry in raw.split(","):
            values = entry.split("|")
            if len(values) != len(self.fields):
                raise ValueError("CSQ entry does not match declared schema")
            fields = tuple(zip(self.fields, values, strict=True))
            allele = values[self.fields.index("ALLELE_NUM")]
            if not allele.isascii() or not allele.isdigit() or len(allele) > 3:
                raise ValueError("Invalid CSQ allele number")
            number = int(allele)
            if not 1 <= number <= alternate_count:
                raise ValueError("CSQ allele number does not match input alleles")
            result = Consequence(number, fields)
            for name in POPULATION_FIELDS:
                _frequency(result.value(name))
            output.append(result)
        return tuple(output)


def _frequency(value: str | None) -> float | None:
    if value in {None, "", "."}:
        return None
    try:
        number = float(value)
    except ValueError:
        raise ValueError("Invalid external population frequency") from None
    if not math.isfinite(number) or not 0 <= number <= 1:
        raise ValueError("Invalid external population frequency")
    return number


def population_frequency(
    consequences: tuple[Consequence, ...], *, allele_number: int
) -> float | None:
    """Maximum observed gnomAD frequency for this allele; absence stays unknown."""
    values = [
        frequency
        for item in consequences
        if item.allele_number == allele_number
        for name in POPULATION_FIELDS
        if (frequency := _frequency(item.value(name))) is not None
    ]
    return max(values, default=None)


def contig_plan(names: tuple[str, ...]) -> dict[str, str | None]:
    """Map standard aliases; None means preserve for deferred handling, never discard.

    This is a plan only. It does not authorize renaming, filtering or changing coordinates.
    Multiple source contigs mapping to one target require explicit reconciliation.
    """
    if len(names) > 100000 or len(names) != len(set(names)):
        raise ValueError("Ambiguous or oversized contig dictionary")
    result: dict[str, str | None] = {}
    seen = set()
    for name in names:
        target = name.removeprefix("chr")
        if target == "M":
            target = "MT"
        if target not in STANDARD_CONTIGS:
            result[name] = None
            continue
        if target in seen:
            raise ValueError("Contig aliases collide; explicit reconciliation required")
        seen.add(target)
        result[name] = target
    return result
