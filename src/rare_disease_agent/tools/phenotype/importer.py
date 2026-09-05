"""Explicit public-only HPO release downloader with provenance."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx

from rare_disease_agent.reporting.audit import AuditWriter
from rare_disease_agent.tools.phenotype.schemas import PhenotypeDatasetProvenance

PUBLIC_HPO_HOSTS = {
    "github.com",
    "objects.githubusercontent.com",
    "purl.obolibrary.org",
    "raw.githubusercontent.com",
    "release-assets.githubusercontent.com",
}


def import_public_hpo_file(
    *,
    url: str,
    destination: Path | str,
    release: str,
    license_reference: str,
    expected_sha256: str | None = None,
    maximum_bytes: int = 100 * 1024 * 1024,
) -> PhenotypeDatasetProvenance:
    """Download one allow-listed public HPO artifact; never accepts credentials."""

    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in PUBLIC_HPO_HOSTS:
        raise ValueError("Only HTTPS files from allow-listed public HPO hosts are permitted")
    if parsed.username or parsed.password or parsed.query:
        raise ValueError("Authenticated or query-tokenized HPO URLs are not permitted")
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".download")
    digest = hashlib.sha256()
    size = 0
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=120) as response:
            response.raise_for_status()
            final_url = urlparse(str(response.url))
            if final_url.scheme != "https" or final_url.hostname not in PUBLIC_HPO_HOSTS:
                raise ValueError("Public HPO redirect resolved to a non-allow-listed host")
            with temporary.open("wb") as handle:
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > maximum_bytes:
                        raise ValueError("Public HPO artifact exceeded the configured size limit")
                    digest.update(chunk)
                    handle.write(chunk)
        checksum = digest.hexdigest()
        if expected_sha256 and checksum != expected_sha256.lower():
            raise ValueError("Public HPO artifact checksum did not match the pinned release")
        temporary.replace(target)
    finally:
        if temporary.exists():
            temporary.unlink()
    provenance = PhenotypeDatasetProvenance(
        source=url,
        release=release,
        downloaded_at=datetime.now(UTC),
        checksum=checksum,
        license_reference=license_reference,
        synthetic=False,
    )
    AuditWriter._write_json(target.with_suffix(target.suffix + ".manifest.json"), provenance)
    return provenance
