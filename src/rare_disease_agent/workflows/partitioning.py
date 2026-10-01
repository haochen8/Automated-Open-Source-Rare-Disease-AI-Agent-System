"""Private, streaming source accounting and disjoint VCF partition preparation.

No normalization, reference validation, annotation, filtering or ranking occurs here.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Annotated

import psutil
from pydantic import BaseModel, ConfigDict, Field, model_validator

from rare_disease_agent.resource_management import atomic_json, sha256
from rare_disease_agent.tools.variants.ingest import info_fields, vcf_rows
from rare_disease_agent.workflows.authorized import _outside_git
from rare_disease_agent.workflows.benchmark import AUTOSOMES, TAGS
from rare_disease_agent.workflows.phase5 import code_checksum
from rare_disease_agent.workflows.recovery import RestartableRun

VERSION = "disjoint-source-partitions-v1"
MAX_OUTPUT = 512 * 1024**2
MAX_HEADER = 4 * 1024**2
MAX_PARTITIONS = 100_000
TAG_HEADER = (
    "##INFO=<ID=RDA_SOURCE_ROW,Number=1,Type=Integer,"
    'Description="Original global record ordinal">\n'
    '##INFO=<ID=RDA_ALT_IDX,Number=A,Type=Integer,Description="Original alternate ordinal">\n'
)


class CoverageInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source: Path
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    output: Path
    records_per_shard: int = Field(default=500, strict=True, ge=1, le=500)
    shard_record_limits: dict[int, Annotated[int, Field(strict=True, ge=1, le=500)]] = Field(
        default_factory=dict, max_length=8
    )
    confirmed_local_research_use: bool

    @model_validator(mode="after")
    def bounded_overrides(self):
        if any(
            not 1 <= number <= MAX_PARTITIONS or limit > self.records_per_shard
            for number, limit in self.shard_record_limits.items()
        ):
            raise ValueError("Shard overrides may only reduce the default record limit")
        return self

    def record_limit(self, number: int) -> int:
        return self.shard_record_limits.get(number, self.records_per_shard)


class _Budget:
    def __init__(self, directory: Path):
        self.directory = directory
        self.started = time.monotonic()
        self.written = 0
        self.check()

    def check(self):
        if (
            time.monotonic() - self.started > 3600
            or psutil.virtual_memory().available < 256 * 1024**2
            or psutil.Process().memory_info().rss > 1024**3
            or psutil.disk_usage(self.directory).free < 25 * 1024**3
            or self.written > MAX_OUTPUT
        ):
            raise RuntimeError("Coverage preparation resource limit reached")

    def write(self, stream, text):
        self.written += len(text.encode())
        self.check()
        stream.write(text)


def _private(*paths):
    for name in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING"):
        if os.environ.get(name, "").lower() not in {"", "false", "0"}:
            raise ValueError("External tracing must be disabled")
    for path in paths:
        _outside_git(path.resolve())


def _records(source: Path, header: list[str], budget: _Budget):
    """Validate structure; preserve sample fields and source names without inferring context."""
    seen_header = False
    aliases = {}
    header_size = ordinal = 0
    for line in vcf_rows(source):
        if line.startswith("#"):
            if seen_header or not line:
                raise ValueError("Unexpected VCF header ordering")
            if any(line.startswith("##INFO=<ID=" + tag + ",") for tag in TAGS):
                raise ValueError("Reserved source provenance field")
            header_size += len(line.encode()) + 1
            if header_size > MAX_HEADER:
                raise ValueError("VCF header exceeds preparation budget")
            if not header and not re.fullmatch(r"##fileformat=VCFv4\.[1-5]", line):
                raise ValueError("Unsupported VCF file format")
            if line.startswith("#CHROM\t"):
                fields = line.split("\t")
                if (
                    fields[:9]
                    != ["#CHROM", "POS", "ID", "REF", "ALT", "QUAL", "FILTER", "INFO", "FORMAT"]
                    or len(fields) != 10
                    or not fields[9]
                ):
                    raise ValueError("Coverage preparation requires one sample")
                header.append(TAG_HEADER.rstrip("\n"))
                seen_header = True
            elif not line.startswith("##"):
                raise ValueError("Unsupported VCF header")
            header.append(line)
            continue
        if not seen_header:
            raise ValueError("Missing VCF sample header")
        ordinal += 1
        if ordinal % 1024 == 1:
            budget.check()
        fields = line.split("\t")
        if len(fields) != 10 or not fields[0] or not fields[1].isdecimal() or int(fields[1]) < 1:
            raise ValueError("Malformed VCF record")
        info = info_fields(fields[7])
        if any(tag in info for tag in TAGS):
            raise ValueError("Reserved source provenance value")
        alternates = fields[4].split(",")
        if (
            not fields[3]
            or any(not a for a in alternates)
            or len(set(alternates)) != len(alternates)
            or ("." in alternates and len(alternates) != 1)
        ):
            raise ValueError("Malformed source alleles")
        alternate_count = 0 if alternates == ["."] else len(alternates)
        formats, calls = fields[8].split(":"), fields[9].split(":")
        if len(formats) != len(calls) or len(set(formats)) != len(formats) or "GT" not in formats:
            raise ValueError("Malformed source genotype fields")
        gt = calls[formats.index("GT")]
        if not re.fullmatch(r"(?:\d+|\.)(?:[/|](?:\d+|\.))*", gt) or ("/" in gt and "|" in gt):
            raise ValueError("Malformed source genotype")
        alleles = re.split(r"[/|]", gt)
        if any(a != "." and int(a) > alternate_count for a in alleles):
            raise ValueError("Source genotype exceeds alternate count")
        chrom = fields[0]
        canonical = chrom.removeprefix("chr")
        if canonical in AUTOSOMES and aliases.setdefault(canonical, chrom) != chrom:
            raise ValueError("Ambiguous autosomal contig aliases")
        if canonical in {"X", "Y"}:
            reason = "sex_chromosome_context_pending"
        elif canonical in {"M", "MT"}:
            reason = "mitochondrial_context_pending"
        elif canonical not in AUTOSOMES:
            reason = "nonstandard_contig_pending"
        elif chrom != canonical:
            reason = "reference_alias_pending"
        elif not all(re.fullmatch("[ACGT]+", a) for a in [fields[3], *alternates]):
            reason = "nonsequence_allele_pending"
        elif len(alleles) != 2:
            reason = "autosomal_ploidy_pending"
        else:
            reason = "eligible_autosomal_sequence"
        tags = f"RDA_SOURCE_ROW={ordinal};RDA_ALT_IDX=" + ",".join(
            str(i + 1) for i in range(alternate_count)
        )
        fields[7] = tags if fields[7] == "." else fields[7] + ";" + tags
        yield ordinal, chrom, reason, alternate_count, "\t".join(fields) + "\n"
    if not seen_header or ordinal == 0:
        raise ValueError("Empty or incomplete VCF")


def prepare_coverage(spec: CoverageInput) -> dict:
    """Atomically publish a complete accounting plan without copying variant records."""
    if not spec.confirmed_local_research_use:
        raise PermissionError("Explicit local preparation authorization required")
    source, output = spec.source.resolve(strict=True), spec.output.resolve()
    _private(source, output)
    if output.exists() or source.is_relative_to(output):
        raise ValueError("Coverage plan requires a fresh separate directory")
    budget = _Budget(output.parent)
    implementation = code_checksum()
    if sha256(source) != spec.source_sha256:
        raise ValueError("Source checksum mismatch")
    staging = Path(tempfile.mkdtemp(prefix=".coverage-", dir=output.parent))
    try:
        header = []
        counts = {}
        current = None
        digest = None
        shards = 0
        with (staging / "segments.jsonl").open("x") as stream:

            def flush():
                if current is not None:
                    record = current | {"records_sha256": digest.hexdigest()}
                    budget.write(stream, json.dumps(record, sort_keys=True) + "\n")

            for row, chrom, reason, alternate_count, text in _records(source, header, budget):
                eligible = reason == "eligible_autosomal_sequence"
                if (
                    current is None
                    or current["chromosome"] != chrom
                    or current["disposition"] != reason
                    or (eligible and current["source_records"] >= spec.record_limit(shards))
                ):
                    flush()
                    if eligible:
                        shards += 1
                        if shards > MAX_PARTITIONS:
                            raise ValueError("Partition inventory exceeds work budget")
                    current = {
                        "shard": shards if eligible else None,
                        "chromosome": chrom,
                        "disposition": reason,
                        "first_source_row": row,
                        "last_source_row": row,
                        "source_records": 0,
                        "alternate_alleles": 0,
                    }
                    digest = hashlib.sha256()
                current["last_source_row"] = row
                current["source_records"] += 1
                current["alternate_alleles"] += alternate_count
                digest.update(text.encode())
                total = counts.setdefault(reason, {"source_records": 0, "alternate_alleles": 0})
                total["source_records"] += 1
                total["alternate_alleles"] += alternate_count
            flush()
        if any(number > shards for number in spec.shard_record_limits):
            raise ValueError("Record-limit override names a nonexistent shard")
        with (staging / "header.txt").open("x") as stream:
            budget.write(stream, "\n".join(header) + "\n")
        if sha256(source) != spec.source_sha256 or code_checksum() != implementation:
            raise ValueError("Source or implementation changed during planning")
        result = {
            "version": VERSION,
            "source": str(source),
            "source_sha256": spec.source_sha256,
            "code_sha256": implementation,
            "records_per_shard": spec.records_per_shard,
            "shard_record_limits": spec.shard_record_limits,
            "shards": shards,
            "counts": counts,
            "source_records": sum(x["source_records"] for x in counts.values()),
            "alternate_alleles": sum(x["alternate_alleles"] for x in counts.values()),
            "reference_validated": False,
            "whole_genome_analysis_complete": False,
            "scope": "source accounting only; eligibility does not establish "
            "reference/cache or clinical support",
            "outputs": {name: sha256(staging / name) for name in ("segments.jsonl", "header.txt")},
        }
        atomic_json(staging / "manifest.json", result)
        budget.written = sum(path.stat().st_size for path in staging.iterdir())
        budget.check()
        staging.rename(output)
        return result
    except BaseException:
        shutil.rmtree(staging)
        raise


def materialize_partitions(
    plan: Path, output: Path, *, first: int = 1, last: int = 1, authorized: bool = False
) -> None:
    """One source scan per request, at most 64 shards; committed shards are immutable."""
    if not authorized:
        raise PermissionError("Explicit local preparation authorization required")
    plan, output = plan.resolve(strict=True), output.resolve()
    _private(plan, output)
    if plan.is_relative_to(output) or output.is_relative_to(plan):
        raise ValueError("Plan and materialization directories must be separate")
    if (
        type(first) is not int
        or type(last) is not int
        or not 1 <= first <= last
        or last - first >= 64
    ):
        raise ValueError("Requested partition range exceeds work budget")
    manifest_path = plan / "manifest.json"
    if manifest_path.is_symlink() or manifest_path.stat().st_size > MAX_HEADER:
        raise ValueError("Coverage manifest exceeds work budget")
    manifest_digest = sha256(manifest_path)
    manifest = json.loads(manifest_path.read_text())
    schedule = CoverageInput(
        source=manifest["source"],
        source_sha256=manifest["source_sha256"],
        output=output,
        records_per_shard=manifest["records_per_shard"],
        shard_record_limits=manifest.get("shard_record_limits", {}),
        confirmed_local_research_use=True,
    )
    if (
        manifest["version"] != VERSION
        or manifest["code_sha256"] != code_checksum()
        or last > manifest["shards"]
    ):
        raise ValueError("Coverage plan identity or requested range mismatch")
    if set(manifest["outputs"]) != {"header.txt", "segments.jsonl"}:
        raise ValueError("Unexpected coverage plan inventory")
    for name, digest in manifest["outputs"].items():
        path = plan / name
        if path.is_symlink() or sha256(path) != digest:
            raise ValueError("Coverage plan artifact integrity mismatch")
    source = Path(manifest["source"]).resolve(strict=True)
    _private(source)
    if source.is_relative_to(output) or sha256(source) != manifest["source_sha256"]:
        raise ValueError("Coverage source identity mismatch")
    budget = _Budget(output.parent)
    output.mkdir(mode=0o700, exist_ok=True)
    with (output / ".run.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        runner = RestartableRun(
            output,
            run_id="coverage-materialization",
            configuration={
                "plan": str(plan),
                "manifest_sha256": manifest_digest,
                "source_sha256": manifest["source_sha256"],
                "code_sha256": code_checksum(),
                "scope": "partition preparation only",
            },
        )
        budget.written = sum(p.stat().st_size for p in output.rglob("*") if p.is_file())
        budget.check()
        header = []
        records = iter(_records(source, header, budget))
        header_text = (plan / "header.txt").read_text()
        if len(header_text.encode()) > MAX_HEADER + len(TAG_HEADER):
            raise ValueError("Partition header exceeds budget")
        previous = 0
        observed_counts = {}
        observed_shards = 0
        try:
            with (plan / "segments.jsonl").open() as segments:
                while line := segments.readline(MAX_HEADER + 1):
                    if len(line) > MAX_HEADER:
                        raise ValueError("Partition metadata exceeds budget")
                    segment = json.loads(line)
                    if (
                        segment["first_source_row"] != previous + 1
                        or segment["last_source_row"] - previous != segment["source_records"]
                    ):
                        raise ValueError("Partition source intervals do not reconcile")
                    selected = segment["shard"] is not None and first <= segment["shard"] <= last
                    if segment["shard"] is not None:
                        observed_shards += 1
                        if segment["shard"] != observed_shards or not 1 <= segment[
                            "source_records"
                        ] <= schedule.record_limit(observed_shards):
                            raise ValueError("Partition shard sequence does not reconcile")

                    def consume(directory=None, segment=segment):
                        digest = hashlib.sha256()
                        alternate_total = 0
                        handle = (directory / "subset.vcf").open("x") if directory else None
                        try:
                            if handle:
                                budget.write(handle, header_text)
                            for ordinal in range(
                                segment["first_source_row"], segment["last_source_row"] + 1
                            ):
                                row, chrom, reason, alternate_count, text = next(records)
                                if (row, chrom, reason) != (
                                    ordinal,
                                    segment["chromosome"],
                                    segment["disposition"],
                                ):
                                    raise ValueError("Partition record context mismatch")
                                alternate_total += alternate_count
                                digest.update(text.encode())
                                if handle:
                                    budget.write(handle, text)
                        finally:
                            if handle:
                                handle.close()
                        if (
                            alternate_total != segment["alternate_alleles"]
                            or digest.hexdigest() != segment["records_sha256"]
                            or "\n".join(header) + "\n" != header_text
                        ):
                            raise ValueError("Partition content integrity mismatch")
                        if directory:
                            atomic_json(
                                directory / "receipt.json",
                                segment
                                | {
                                    "source_sha256": manifest["source_sha256"],
                                    "subset_sha256": sha256(directory / "subset.vcf"),
                                    "whole_genome_analysis_complete": False,
                                },
                            )

                    name = f"shard_{segment['shard']:06d}" if selected else None
                    if name and name not in runner.journal.stages:
                        runner.stage(name, consume)
                    else:
                        consume()
                    previous = segment["last_source_row"]
                    count = observed_counts.setdefault(
                        segment["disposition"], {"source_records": 0, "alternate_alleles": 0}
                    )
                    for key in count:
                        count[key] += segment[key]
            if (
                next(records, None) is not None
                or previous != manifest["source_records"]
                or observed_counts != manifest["counts"]
                or observed_shards != manifest["shards"]
                or any(number > observed_shards for number in schedule.shard_record_limits)
            ):
                raise ValueError("Full source accounting mismatch")
            if (
                sha256(source) != manifest["source_sha256"]
                or code_checksum() != manifest["code_sha256"]
                or sha256(manifest_path) != manifest_digest
                or any(sha256(plan / n) != h for n, h in manifest["outputs"].items())
            ):
                raise ValueError("Partition inputs changed during preparation")
            budget.written = sum(
                path.stat().st_size for path in output.rglob("*") if path.is_file()
            )
            budget.check()
            runner.verify()
            if len(runner.journal.stages) == manifest["shards"]:
                runner.complete()
        finally:
            records.close()
