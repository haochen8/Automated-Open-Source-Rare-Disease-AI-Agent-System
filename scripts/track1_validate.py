#!/usr/bin/env python3
"""Check one supplied CSV against a provisional local Track 1 format contract.

This stdlib-only tool does not assess source identity, coverage, scientific
interpretation, official compliance, or submission readiness. It never rewrites
input or selects candidates. Input and field bounds are local engineering limits.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import stat
import sys
from contextlib import contextmanager
from pathlib import Path

CONTRACT = "track1-provisional-local-v1"
ORDER_POLICY = "descending-epcr-preserve-input-ties"
MAX_INPUT_BYTES = 1024**2
MAX_FIELD_BYTES = 8 * 1024
HEADERS = (
    "proband_id",
    "chrom_1",
    "pos_1",
    "ref_1",
    "alt_1",
    "chrom_2",
    "pos_2",
    "ref_2",
    "alt_2",
    "epcr",
    "finding_type",
    "notes",
)
SUCCESS = {
    "contract": CONTRACT,
    "format_validation": "valid",
    "official_contract": "unverified",
    "source_binding": "not_assessed",
    "coverage": "not_assessed",
    "submission_readiness": "not_assessed",
}
REFUSAL = "TRACK1_FORMAT_VALIDATION_REFUSED\n"


class FormatRefusal(Exception):
    """Deliberately carries no input-derived diagnostic."""


def require(condition: bool) -> None:
    if not condition:
        raise FormatRefusal


def signature(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


@contextmanager
def regular_input(path: str):
    """Open each path component without following symlinks, including parents."""
    require(bool(path) and ".." not in Path(path).parts)
    parts = Path(os.path.abspath(path)).parts
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    parent = os.open(parts[0], flags | os.O_DIRECTORY)
    try:
        for part in parts[1:-1]:
            child = os.open(part, flags | os.O_DIRECTORY, dir_fd=parent)
            os.close(parent)
            parent = child
        descriptor = os.open(parts[-1], flags, dir_fd=parent)
    finally:
        os.close(parent)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and 0 < info.st_size <= MAX_INPUT_BYTES)
        yield stream


def endpoint(values: list[str]) -> tuple[str, str, str, str]:
    chrom, position, reference, alternate = values
    require(all(values))
    require(all("0" <= char <= "9" for char in position))
    # Decimal-string identity avoids unbounded integer conversion. No input is rewritten.
    numeric_position = position.lstrip("0")
    require(bool(numeric_position))
    # These comparisons mirror parser identity, not genomic/biological equivalence.
    return chrom, numeric_position, reference.upper(), alternate.upper()


def structural(value: str) -> bool:
    return value == value.strip() and all(32 <= ord(char) != 127 for char in value)


def validate_contents(raw: bytes, expected_proband: str, order_policy: str) -> None:
    require(order_policy == ORDER_POLICY)
    require(bool(expected_proband) and structural(expected_proband))
    require(len(expected_proband.encode("utf-8")) <= MAX_FIELD_BYTES)
    previous_limit = csv.field_size_limit()
    csv.field_size_limit(MAX_FIELD_BYTES)
    try:
        rows = csv.reader(io.StringIO(raw.decode("utf-8"), newline=""), strict=True)
        require(tuple(next(rows)) == HEADERS)
        count = 0
        seen = set()
        previous_epcr = 1.0
        for row in rows:
            count += 1
            require(count <= 10 and len(row) == len(HEADERS))
            require(all(len(value.encode("utf-8")) <= MAX_FIELD_BYTES for value in row))
            require(all("\0" not in value for value in row))
            # Local whitespace/control restrictions exempt free-text notes.
            require(all(structural(value) for value in row[:-1]))
            require(row[0] == expected_proband and row[10] in {"primary", "secondary"})
            first = endpoint(row[1:5])
            second_fields = row[5:9]
            require(all(second_fields) or not any(second_fields))
            second = endpoint(second_fields) if any(second_fields) else None
            require(second is None or second != first)
            candidate = frozenset((first, second)) if second is not None else frozenset((first,))
            require(candidate not in seen)
            seen.add(candidate)
            epcr = float(row[9])
            require(math.isfinite(epcr) and 0 < epcr <= previous_epcr <= 1)
            previous_epcr = epcr
        require(1 <= count <= 10)
    finally:
        csv.field_size_limit(previous_limit)


def validate_file(path: str, expected_proband: str, order_policy: str) -> None:
    with regular_input(path) as stream:
        before = signature(os.fstat(stream.fileno()))
        raw = stream.read(MAX_INPUT_BYTES + 1)
        require(len(raw) == before[3] and signature(os.fstat(stream.fileno())) == before)
        validate_contents(raw, expected_proband, order_policy)
        require(signature(os.fstat(stream.fileno())) == before)
        stream.seek(0)
        require(stream.read(MAX_INPUT_BYTES + 1) == raw)
        require(signature(os.fstat(stream.fileno())) == before)
        # Also reject replacement of the pathname while the original descriptor stayed open.
        with regular_input(path) as current:
            require(signature(os.fstat(current.fileno())) == before)
            require(current.read(MAX_INPUT_BYTES + 1) == raw)
            require(signature(os.fstat(current.fileno())) == before)


class PrivateArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise FormatRefusal


def main(argv: list[str] | None = None) -> int:
    parser = PrivateArgumentParser(
        prog="track1_validate.py", description=__doc__, allow_abbrev=False
    )
    parser.add_argument("--input", required=True, metavar="CSV")
    parser.add_argument("--expected-proband", required=True, metavar="ID")
    parser.add_argument("--order-policy", required=True, choices=[ORDER_POLICY])
    try:
        args = parser.parse_args(argv)
        validate_file(args.input, args.expected_proband, args.order_policy)
        print(json.dumps(SUCCESS, sort_keys=True))
        return 0
    except (Exception, KeyboardInterrupt):
        sys.stderr.write(REFUSAL)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
