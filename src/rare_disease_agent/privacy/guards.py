"""Reject restricted biomedical data and common secrets before commit."""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from pydantic import BaseModel

BLOCKED_DIRECTORY_NAMES = {
    "private_data",
    "models",
    "cache",
    "secrets",
    "tokens",
}
BLOCKED_SUFFIXES = (
    ".vcf",
    ".vcf.gz",
    ".gvcf",
    ".gvcf.gz",
    ".bcf",
    ".bam",
    ".bai",
    ".cram",
    ".crai",
    ".fastq",
    ".fastq.gz",
    ".fq",
    ".fq.gz",
    ".ped",
    ".parquet",
    ".duckdb",
    ".duckdb.wal",
    ".resource",
    ".tar.gz",
    ".zip",
)
SECRET_PATTERNS = {
    "Hugging Face access token": re.compile(r"\bhf_[A-Za-z0-9]{20,}\b"),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "generic credential assignment": re.compile(
        r"(?i)\b(?:api[_-]?key|access[_-]?token|password)\s*[:=]\s*['\"]?[A-Za-z0-9_\-/+]{16,}"
    ),
}
MAX_TEXT_SCAN_BYTES = 2 * 1024 * 1024
DATA_LIKE_SUFFIXES = {".csv", ".json", ".jsonl", ".tsv", ".txt", ".yaml", ".yml"}


class GuardFinding(BaseModel):
    path: str
    reason: str


def _is_allowed_documentation(path: Path) -> bool:
    normalized = path.as_posix().lstrip("./")
    return normalized == "data/README.md" or normalized.endswith(".example")


def _is_synthetic_fixture(path: Path, text: str) -> bool:
    normalized = path.as_posix().lstrip("./")
    allowed_location = normalized.startswith("tests/fixtures/") or normalized.startswith(
        "src/rare_disease_agent/resources/"
    )
    return allowed_location and "##synthetic=true" in text


def _is_model_source_module(path: Path) -> bool:
    normalized = path.as_posix().lstrip("./")
    return normalized.startswith("src/rare_disease_agent/models/") and path.suffix == ".py"


def inspect_path(path: Path, *, repository_root: Path) -> list[GuardFinding]:
    findings: list[GuardFinding] = []
    try:
        relative = path.resolve().relative_to(repository_root.resolve())
    except ValueError:
        return [GuardFinding(path=str(path), reason="path is outside repository")]
    normalized = relative.as_posix()
    if _is_allowed_documentation(relative):
        return findings

    parts = set(relative.parts)
    if (
        parts & BLOCKED_DIRECTORY_NAMES
        and not _is_model_source_module(relative)
        or normalized.startswith("runs/private/")
    ):
        findings.append(GuardFinding(path=normalized, reason="restricted/generated directory"))
    lower_name = normalized.lower()
    if any(lower_name.endswith(suffix) for suffix in BLOCKED_SUFFIXES):
        findings.append(GuardFinding(path=normalized, reason="restricted genomic/data file type"))
    if not path.is_file() or path.stat().st_size > MAX_TEXT_SCAN_BYTES:
        return findings
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return findings
    if (
        path.suffix.lower() in DATA_LIKE_SUFFIXES
        and "##fileformat=VCF" in text
        and not _is_synthetic_fixture(relative, text)
    ):
        findings.append(
            GuardFinding(path=normalized, reason="VCF content without synthetic marker")
        )
    for label, pattern in SECRET_PATTERNS.items():
        if pattern.search(text):
            findings.append(GuardFinding(path=normalized, reason=f"possible {label}"))
    return findings


def scan_paths(paths: Iterable[Path], *, repository_root: Path) -> list[GuardFinding]:
    findings: list[GuardFinding] = []
    for path in paths:
        findings.extend(inspect_path(path, repository_root=repository_root))
    return findings
