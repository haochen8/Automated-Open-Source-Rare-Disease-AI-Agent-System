"""Explicit, checksum-pinned public resource lifecycle. No implicit networking."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urljoin, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

PUBLIC_HOSTS = frozenset(
    {"github.com", "raw.githubusercontent.com", "release-assets.githubusercontent.com"}
)


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def safe_url(url: str, *, release: str | None = None) -> str:
    parsed = urlsplit(url)
    decoded = unquote(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in PUBLIC_HOSTS
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.port not in (None, 443)
        or any(ord(c) < 33 for c in decoded)
        or "\\" in decoded
        or any(part in {".", ".."} for part in unquote(parsed.path).split("/"))
        or re.search(r"(?:^|/)(latest|main|master)(?:/|$)", decoded, re.I)
    ):
        raise ValueError("Resource URL must be public, pinned, credential-free and allow-listed")
    if release is not None:
        prefix = "/obophenotype/human-phenotype-ontology/"
        if parsed.hostname not in {
            "github.com",
            "raw.githubusercontent.com",
        } or not parsed.path.startswith(prefix):
            raise ValueError("Only official HPO release sources are approved")
        if release not in parsed.path.split("/"):
            raise ValueError("Immutable release must occur in source URL")
    return url


class ResourceLock(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    name: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    source_url: str
    release: str = Field(pattern=r"^(?:v?\d{4}-\d{2}-\d{2}|[0-9a-f]{40})$")
    expected_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_size: int = Field(gt=0, le=512 * 1024**2)
    license_reference: str = Field(min_length=1)
    citation: str = Field(min_length=1)
    parser_version: Literal["hpo-normalizer-v1"] = "hpo-normalizer-v1"
    kind: Literal["obo", "genes", "diseases"]

    @model_validator(mode="after")
    def validate_source(self) -> ResourceLock:
        safe_url(self.source_url, release=self.release)
        return self


class ResourceReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    lock_hash: str
    retrieved_at: datetime
    observed_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(gt=0)
    output_checksum: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    lock: ResourceLock


def lock_hash(lock: ResourceLock) -> str:
    return hashlib.sha256(lock.model_dump_json().encode()).hexdigest()


def read_locks(path: Path) -> list[ResourceLock]:
    locks = [ResourceLock.model_validate(item) for item in json.loads(path.read_text())]
    if len({item.name for item in locks}) != len(locks):
        raise ValueError("Duplicate resource names")
    return locks


def atomic_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


class ResourceManager:
    def __init__(self, directory: Path):
        self.directory = directory

    def paths(self, lock: ResourceLock) -> tuple[Path, Path]:
        stem = f"{lock.name}-{lock_hash(lock)[:16]}"
        return self.directory / (stem + ".resource"), self.directory / (stem + ".receipt.json")

    def verify(self, lock: ResourceLock) -> ResourceReceipt:
        artifact, receipt_path = self.paths(lock)
        receipt = ResourceReceipt.model_validate_json(receipt_path.read_text())
        if receipt.lock != lock or receipt.lock_hash != lock_hash(lock):
            raise ValueError("Stale resource lock")
        if artifact.stat().st_size != lock.expected_size or receipt.size != lock.expected_size:
            raise ValueError("Resource size mismatch")
        if (
            sha256(artifact) != lock.expected_sha256
            or receipt.observed_sha256 != lock.expected_sha256
        ):
            raise ValueError("Resource checksum mismatch")
        return receipt

    def status(self, lock: ResourceLock) -> dict:
        try:
            self.verify(lock)
        except FileNotFoundError:
            state = "missing"
        except (ValueError, OSError):
            state = "invalid"
        else:
            state = "verified"
        return {
            "name": lock.name,
            "release": lock.release,
            "status": state,
            "lock_hash": lock_hash(lock),
        }

    def fetch(self, lock: ResourceLock, *, client: httpx.Client | None = None) -> ResourceReceipt:
        """Caller explicitly selects one resource. Redirects are checked before each request."""
        self.directory.mkdir(parents=True, exist_ok=True)
        artifact, receipt_path = self.paths(lock)
        if artifact.exists() or receipt_path.exists():
            return self.verify(lock)
        temporary = artifact.with_name(artifact.name + "." + uuid.uuid4().hex + ".partial")
        owned = client is None
        client = client or httpx.Client(timeout=30, follow_redirects=False, trust_env=False)
        try:
            url = lock.source_url
            for _ in range(6):
                safe_url(url)
                with client.stream(
                    "GET", url, follow_redirects=False, headers={"Accept-Encoding": "identity"}
                ) as response:
                    if response.is_redirect:
                        url = urljoin(url, response.headers["location"])
                        continue
                    response.raise_for_status()
                    if response.headers.get("content-encoding", "identity") != "identity":
                        raise ValueError("Compressed HTTP transport is not accepted")
                    if (
                        int(response.headers.get("content-length", lock.expected_size))
                        != lock.expected_size
                    ):
                        raise ValueError("Resource size mismatch")
                    size = 0
                    digest = hashlib.sha256()
                    with temporary.open("xb") as output:
                        for block in response.iter_bytes(chunk_size=65536):
                            size += len(block)
                            if size > lock.expected_size:
                                raise ValueError("Oversized resource")
                            digest.update(block)
                            output.write(block)
                    if size != lock.expected_size or digest.hexdigest() != lock.expected_sha256:
                        raise ValueError("Resource checksum or size mismatch")
                    receipt = ResourceReceipt(
                        lock=lock,
                        lock_hash=lock_hash(lock),
                        retrieved_at=datetime.now(UTC),
                        observed_sha256=digest.hexdigest(),
                        size=size,
                    )
                    temporary.replace(artifact)
                    atomic_json(receipt_path, receipt.model_dump(mode="json"))
                    return receipt
            raise ValueError("Resource redirect limit exceeded")
        finally:
            temporary.unlink(missing_ok=True)
            if owned:
                client.close()
