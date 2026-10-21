#!/usr/bin/env python3
"""Export a sealed Track 1 selection with an explicit chromosome presentation map.

Only the saved-computational-submission-064-v1 final export stage is reproduced.
Source binding, scientific analysis, hypothesis selection and EPCR allocation are
not rerun or independently established. All diagnostics are fixed, data-free text.
"""

from __future__ import annotations

import argparse
import csv
import ctypes
import hashlib
import io
import json
import os
import re
import shutil
import stat
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

import track1_validate as validator

VERSION = "track1-sealed-selection-export-v1"
SOURCE_VERSION = "saved-computational-submission-064-v1"
SOURCE_STATUS = "completed_saved_computational_submission_draft"
POLICY_VERSION = "track1-computational-hypotheses-v1"
MAPPING_POLICY = "grch38-primary-chr-prefix-v1"
MAX_INPUT_BYTES = 4 * 1024**2
INPUT_FILES = frozenset({"draft.csv", "audit.json", "manifest.json"})
CHROMOSOME_MAP = {
    name: "chr" + chrom
    for chrom in [*(str(number) for number in range(1, 23)), "X", "Y"]
    for name in (chrom, "chr" + chrom)
}
SUCCESS = {"status": "completed_sealed_selection_export", "source_analysis_rerun": False}
REFUSAL = "TRACK1_EXPORT_REFUSED\n"


class ExportRefusal(ValueError):
    """Never carries supplied paths, case labels, alleles or exception messages."""


def require(condition: bool) -> None:
    if not condition:
        raise ExportRefusal


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def strict_json(raw: bytes) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result)
            result[key] = value
        return result

    def refuse_constant(_value):
        raise ExportRefusal

    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=refuse_constant)
    require(type(value) is dict)
    return value


def json_bytes(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def outside_git(path: Path) -> None:
    require(not any(os.path.lexists(parent / ".git") for parent in (path, *path.parents)))


@contextmanager
def directory(path: Path):
    """Open every directory component without following symlinks."""
    require(path.is_absolute() and ".." not in path.parts)
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY
    descriptor = os.open(path.anchor, flags)
    try:
        for part in path.parts[1:]:
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


def read_file(path: Path) -> bytes:
    with directory(path.parent) as parent:
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            require(stat.S_ISREG(before.st_mode) and 0 < before.st_size <= MAX_INPUT_BYTES)
            raw = stream.read(MAX_INPUT_BYTES + 1)
            require(len(raw) == before.st_size)
            require(validator.signature(before) == validator.signature(os.fstat(stream.fileno())))
            require(
                validator.signature(before)
                == validator.signature(os.stat(path.name, dir_fd=parent, follow_symlinks=False))
            )
    return raw


def endpoint(candidate: dict) -> list[str]:
    require(type(candidate) is dict and type(candidate.get("allele")) is dict)
    allele = candidate["allele"]
    require(set(allele) == {"chrom", "pos", "ref", "alt"})
    require(type(allele["pos"]) is int and allele["pos"] > 0)
    require(all(type(allele[key]) is str for key in ("chrom", "ref", "alt")))
    require(type(candidate.get("source_id")) is str and bool(candidate["source_id"]))
    return [allele["chrom"], str(allele["pos"]), allele["ref"], allele["alt"]]


def render_export(draft: bytes, audit: dict, expected_proband: str) -> tuple[bytes, list[dict]]:
    """Compare saved endpoints/EPCR, then change only chromosome field values."""
    validator.validate_contents(draft, expected_proband, validator.ORDER_POLICY)
    require(audit["schema_version"] == 1 and type(audit["schema_version"]) is int)
    require(audit["version"] == SOURCE_VERSION and audit["status"] == SOURCE_STATUS)
    require(audit["policy_version"] == POLICY_VERSION)
    require(audit["proband_label"] == expected_proband)
    require(audit["proband_label_is_biological_identity"] is False)
    hypotheses = audit["hypotheses"]
    require(type(hypotheses) is list and 1 <= len(hypotheses) <= 10)
    rows = list(csv.reader(io.StringIO(draft.decode("utf-8"), newline=""), strict=True))
    require(len(rows) == len(hypotheses) + 1)
    mappings = []
    used_sources = set()
    used_coordinates = set()
    for rank, (row, hypothesis) in enumerate(zip(rows[1:], hypotheses, strict=True), 1):
        require(type(hypothesis) is dict)
        require(
            type(hypothesis["submission_rank"]) is int and hypothesis["submission_rank"] == rank
        )
        require(type(hypothesis["epcr"]) is str and row[9] == hypothesis["epcr"])
        require(row[10] == "primary")
        for name, offset in (("first", 1), ("second", 5)):
            candidate = hypothesis[name]
            if candidate is None:
                require(name == "second" and row[offset : offset + 4] == ["", "", "", ""])
                continue
            original = endpoint(candidate)
            require(row[offset : offset + 4] == original)
            require(original[0] in CHROMOSOME_MAP)
            exported = CHROMOSOME_MAP[original[0]]
            coordinate = (exported, original[1], original[2].upper(), original[3].upper())
            require(
                candidate["source_id"] not in used_sources and coordinate not in used_coordinates
            )
            used_sources.add(candidate["source_id"])
            used_coordinates.add(coordinate)
            mappings.append(
                {
                    "submission_rank": rank,
                    "endpoint": name,
                    "source_allele": dict(candidate["allele"]),
                    "exported_chromosome": exported,
                    "changed": exported != original[0],
                }
            )
            row[offset] = exported
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerows(rows)
    result = stream.getvalue().encode("utf-8")
    validator.validate_contents(result, expected_proband, validator.ORDER_POLICY)
    return result, mappings


def publish(staging: Path, output: Path) -> None:
    """Atomic no-replace rename; unsupported hosts fail without a racy fallback."""
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        rename = libc.renamex_np
        rename.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
        arguments = (os.fsencode(staging), os.fsencode(output), 0x00000004)  # RENAME_EXCL
    elif sys.platform == "linux":
        rename = libc.renameat2
        rename.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        arguments = (-100, os.fsencode(staging), -100, os.fsencode(output), 1)  # RENAME_NOREPLACE
    else:
        raise ExportRefusal
    rename.restype = ctypes.c_int
    require(rename(*arguments) == 0)


def export(config_path: Path) -> None:
    require(
        all(
            os.environ.get(key, "").lower() in {"", "false", "0"}
            for key in ("LANGCHAIN_TRACING", "LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING")
        )
    )
    config_raw = read_file(config_path)
    config = strict_json(config_raw)
    require(
        set(config)
        == {
            "schema_version",
            "source_directory",
            "source_integrity_sha256",
            "expected_proband",
            "chromosome_mapping_policy",
            "output_directory",
        }
    )
    require(type(config["schema_version"]) is int and config["schema_version"] == 1)
    require(config["chromosome_mapping_policy"] == MAPPING_POLICY)
    require(type(config["source_integrity_sha256"]) is str)
    require(re.fullmatch("[0-9a-f]{64}", config["source_integrity_sha256"]) is not None)
    require(type(config["expected_proband"]) is str)
    source = Path(config["source_directory"])
    output = Path(config["output_directory"])
    for path in (source, output):
        require(path.is_absolute() and ".." not in path.parts)
        outside_git(path)
    require(not source.is_relative_to(output) and not output.is_relative_to(source))
    require(not os.path.lexists(output))
    with directory(source), directory(output.parent):
        pass

    integrity_raw = read_file(source / "integrity_manifest.json")
    require(sha256(integrity_raw) == config["source_integrity_sha256"])
    integrity = strict_json(integrity_raw)
    require(set(integrity) == INPUT_FILES)
    inputs = {name: read_file(source / name) for name in sorted(INPUT_FILES)}
    require(all(integrity[name] == sha256(raw) for name, raw in inputs.items()))
    manifest = strict_json(inputs["manifest.json"])
    require(manifest["version"] == SOURCE_VERSION and manifest["status"] == SOURCE_STATUS)
    require(
        manifest["artifact_sha256"]
        == {name: integrity[name] for name in ("audit.json", "draft.csv")}
    )
    corrected, mappings = render_export(
        inputs["draft.csv"], strict_json(inputs["audit.json"]), config["expected_proband"]
    )
    code = {Path(__file__).resolve(), Path(validator.__file__).resolve()}
    code_inputs = {path: read_file(path) for path in code}
    export_audit = {
        "schema_version": 1,
        "version": VERSION,
        "source_version": SOURCE_VERSION,
        "source_integrity_sha256": sha256(integrity_raw),
        "source_artifact_sha256": integrity,
        "configuration_sha256": sha256(config_raw),
        "code_sha256": {path.name: sha256(raw) for path, raw in code_inputs.items()},
        "chromosome_mapping_policy": MAPPING_POLICY,
        "chromosome_mappings": mappings,
        "nonchromosome_field_values_preserved": True,
        "candidate_order_preserved": True,
        "source_analysis_rerun": False,
        "source_binding_revalidated": False,
        "hypothesis_selection_rerun": False,
        "epcr_recomputed": False,
        "whole_case_coverage_established": False,
        "clinical_or_causal_validity_established": False,
        "official_acceptance_established": False,
    }
    outputs = {"submission.csv": corrected, "export_audit.json": json_bytes(export_audit)}
    outputs["integrity_manifest.json"] = json_bytes(
        {name: sha256(raw) for name, raw in outputs.items()}
    )
    staging = Path(tempfile.mkdtemp(prefix=".track1-export-", dir=output.parent))
    try:
        for name, raw in outputs.items():
            require(0 < len(raw) <= MAX_INPUT_BYTES)
            descriptor = os.open(staging / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        with directory(staging) as staged_directory:
            os.fsync(staged_directory)
        for name, raw in inputs.items():
            require(read_file(source / name) == raw)
        require(read_file(source / "integrity_manifest.json") == integrity_raw)
        require(read_file(config_path) == config_raw)
        for path, raw in code_inputs.items():
            require(read_file(path) == raw)
        outside_git(output)
        with directory(output.parent) as parent:
            publish(staging, output)
            os.fsync(parent)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


class PrivateArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ExportRefusal


def main(argv: list[str] | None = None) -> int:
    parser = PrivateArgumentParser(prog="track1_export.py", description=__doc__, allow_abbrev=False)
    parser.add_argument("--config", required=True, metavar="JSON")
    try:
        args = parser.parse_args(argv)
        export(Path(args.config))
        print(json.dumps(SUCCESS, sort_keys=True))
        return 0
    except (Exception, KeyboardInterrupt):
        sys.stderr.write(REFUSAL)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
