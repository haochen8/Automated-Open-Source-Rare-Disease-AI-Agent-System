"""Serial global analysis of bounded, sealed annotation references without tool execution."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import signal
import sqlite3
import stat
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

import duckdb
import psutil
from pydantic import BaseModel, ConfigDict, Field, model_validator

from rare_disease_agent.resource_management import atomic_json, sha256
from rare_disease_agent.tools.variants.pair_workload import PAIR_WORK_BUDGET, count_pair_work
from rare_disease_agent.workflows.assembly import (
    ASSEMBLY_VERSION,
    CORPUS_ASSEMBLY_VERSION,
    inventory,
    plan_identity,
    read_json,
    seal,
    validate_shard,
    verified,
    verify_assembly,
    verify_partition,
    write_assembly_tables,
)
from rare_disease_agent.workflows.coverage_batch import CoverageBatchInput, PinnedTool
from rare_disease_agent.workflows.partitioning import CoverageInput, _private
from rare_disease_agent.workflows.phase5 import Phase5Input, code_checksum, phase5_run
from rare_disease_agent.workflows.recovery import RestartableRun, RunJournal

MAX_REFERENCES = 100_000
MAX_MANIFEST_BYTES = 64 * 1024**2
MAX_LINE_BYTES = 64 * 1024
MAX_ROOT_BYTES = 4 * 1024**2
MAX_OUTPUT_BYTES = 2 * 1024**3
VERSION = "sealed-annotation-corpus-v1"
ANNOTATION_FILES = {
    "subset.vcf",
    "normalized.vcf",
    "annotated.vcf",
    "run_manifest.json",
    "normalization.receipt.json",
    "annotation.receipt.json",
}


class CorpusPairBudgetExceeded(ValueError):
    """The validated corpus and workload report remain committed; ranking is prohibited."""


class CorpusReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    shard: int = Field(strict=True, ge=1, le=MAX_REFERENCES)
    directory: Path
    integrity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class CorpusInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    plan: Path
    partitions: Path
    reference_manifest: Path
    reference_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    normalization: PinnedTool
    annotation: PinnedTool
    ranking: Phase5Input
    output: Path
    confirmed_local_research_use: bool

    @model_validator(mode="after")
    def bounded_context(self):
        # Reuse all existing offline profile/path/resource admission rules. This object
        # is never dispatched to annotation and cannot authorize external execution.
        CoverageBatchInput(
            plan=self.plan,
            partitions=self.partitions,
            shards=[1],
            normalization=self.normalization,
            annotation=self.annotation,
            ranking=self.ranking,
            output=self.output,
            confirmed_local_research_use=self.confirmed_local_research_use,
            confirmed_annotation_execution=False,
        )
        if not self.reference_manifest.is_absolute():
            raise ValueError("Corpus references require an absolute manifest path")
        return self


def safe_path(path: Path, *, must_exist: bool = True) -> None:
    """Reject symlinks in every existing component, including directory ancestors."""
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Corpus paths must be absolute and canonical")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            if must_exist:
                raise
            continue
        if stat.S_ISLNK(info.st_mode):
            raise ValueError("Corpus artifacts cannot contain symbolic links")


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate corpus metadata field")
        result[key] = value
    return result


def _decode(line: str) -> dict:
    value = json.loads(line, object_pairs_hook=_json_object)
    if not isinstance(value, dict):
        raise ValueError("Expected a corpus metadata object")
    return value


def bounded_json(path: Path, value: dict):
    """Admit the actual serialized root metadata before any atomic write."""
    if len((json.dumps(value, indent=2, sort_keys=True) + "\n").encode()) > MAX_ROOT_BYTES:
        raise ValueError("Corpus root metadata exceeds byte budget")
    atomic_json(path, value)


def reference_records(spec: CorpusInput) -> Iterator[CorpusReference]:
    path = spec.reference_manifest
    safe_path(path)
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_MANIFEST_BYTES:
        raise ValueError("Corpus reference manifest exceeds its byte budget")
    digest = hashlib.sha256()
    count = total = 0
    with path.open("rb") as stream:
        while line := stream.readline(MAX_LINE_BYTES + 1):
            count += 1
            total += len(line)
            if len(line) > MAX_LINE_BYTES or count > MAX_REFERENCES or total > MAX_MANIFEST_BYTES:
                raise ValueError("Corpus reference manifest exceeds its work budget")
            digest.update(line)
            yield CorpusReference.model_validate(_decode(line.decode("utf-8")))
    if not count or digest.hexdigest() != spec.reference_manifest_sha256:
        raise ValueError("Corpus reference manifest identity mismatch")


def _files(directory: Path) -> Iterator[Path]:
    safe_path(directory)
    if not directory.is_dir():
        raise ValueError("Expected an artifact directory")

    def unreadable(error):
        raise error

    for parent, directories, filenames in os.walk(directory, followlinks=False, onerror=unreadable):
        base = Path(parent)
        for name in directories:
            if (base / name).is_symlink():
                raise ValueError("Corpus artifacts cannot contain symbolic links")
        for name in filenames:
            path = base / name
            if not stat.S_ISREG(path.lstat().st_mode):
                raise ValueError("Corpus artifacts must be regular files")
            yield path


def _annotation_files(directory: Path) -> Iterator[Path]:
    safe_path(directory)
    expected = ANNOTATION_FILES | {"integrity_manifest.json"}
    found = set()
    with os.scandir(directory) as entries:
        for entry in entries:
            if entry.name not in expected or not entry.is_file(follow_symlinks=False):
                raise ValueError("Unsupported corpus annotation inventory")
            found.add(entry.name)
            yield directory / entry.name
    if found != expected:
        raise ValueError("Incomplete corpus annotation inventory")


def reference_storage_bytes(spec: CorpusInput) -> int:
    """Conservative logical accounting; duplicate references are not free storage."""
    total = spec.reference_manifest.stat().st_size
    for ref in reference_records(spec):
        for path in _annotation_files(ref.directory):
            total += path.stat().st_size
            if total > MAX_OUTPUT_BYTES:
                raise RuntimeError("Corpus combined storage budget exceeded")
    return total


def corpus_storage_bytes(
    spec: CorpusInput, referenced_bytes: int | None = None, *, live: bool = False
) -> int:
    retained = reference_storage_bytes(spec) if referenced_bytes is None else referenced_bytes
    # Only owned live output may be changing names. References and quiescent scans
    # fail immediately; repeated live churn also fails closed instead of undercounting.
    for attempt in range(3 if live else 1):
        total = retained
        try:
            if spec.output.exists():
                for path in _files(spec.output):
                    total += path.stat().st_size
                    if total > MAX_OUTPUT_BYTES:
                        raise RuntimeError("Corpus combined storage budget exceeded")
            break
        except FileNotFoundError:
            if not live or attempt == 2:
                raise
    if total > MAX_OUTPUT_BYTES:
        raise RuntimeError("Corpus combined storage budget exceeded")
    return total


def _entry_resources(spec: CorpusInput):
    if (
        psutil.virtual_memory().available < 1024**3
        or psutil.disk_usage(spec.output.parent).free < 25 * 1024**3
    ):
        raise RuntimeError("Insufficient live resources for corpus stage")


class _CoordinatorBudget:
    """Bound synchronous admission/journal work; worker stages keep separate clocks."""

    def __init__(self, spec: CorpusInput):
        self.spec = spec
        self.started = 0.0
        self.worker_pid = None

    def _check(self, *_):
        memory = psutil.Process(os.getpid()).memory_info().rss
        if self.worker_pid is not None:
            with suppress(psutil.NoSuchProcess):
                parent = psutil.Process(self.worker_pid)
                for item in [parent, *parent.children(recursive=True)]:
                    with suppress(psutil.NoSuchProcess):
                        memory += item.memory_info().rss
        if (
            time.monotonic() - self.started > 3600
            or memory > 3 * 1024**3
            or psutil.virtual_memory().available < 256 * 1024**2
            or psutil.disk_usage(self.spec.output.parent).free < 25 * 1024**3
        ):
            raise RuntimeError("Corpus coordinator resource boundary reached")
        signal.setitimer(signal.ITIMER_REAL, 0.25)

    def __enter__(self):
        self.previous_handler = signal.getsignal(signal.SIGALRM)
        self.previous_timer = signal.getitimer(signal.ITIMER_REAL)
        if self.previous_timer != (0.0, 0.0):
            raise RuntimeError("Corpus coordinator requires an available interval timer")
        # signal.signal fails closed off the main thread; no unguarded fallback.
        signal.signal(signal.SIGALRM, self._check)
        try:
            self.resume()
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def suspend(self):
        signal.setitimer(signal.ITIMER_REAL, 0)

    def resume(self):
        self.started = time.monotonic()
        self._check()

    def __exit__(self, *_):
        self.suspend()
        signal.signal(signal.SIGALRM, self.previous_handler)
        signal.setitimer(signal.ITIMER_REAL, *self.previous_timer)


def _journal_admission(spec: CorpusInput, stage: Path):
    """Bound the existing recursive journal before publication, without raising its limits."""
    prior = spec.output / "run.json"
    used = prior.stat().st_size if prior.exists() else 0
    # Leave room for timestamps, the stage record and a fixed error class on failure.
    used += 16 * 1024
    _journal_files_admission(stage, stage, used)
    # Allow the bounded journal, audit projection and their atomic temporary files
    # to be written after publication without exceeding the combined storage cap.
    if corpus_storage_bytes(spec) + 4 * MAX_ROOT_BYTES > MAX_OUTPUT_BYTES:
        raise RuntimeError("Corpus publication metadata exceeds combined storage budget")


def _journal_files_admission(directory: Path, base: Path, used: int) -> int:
    for path in _files(directory):
        used += len(json.dumps(str(path.relative_to(base))).encode()) + 96
        if used > MAX_ROOT_BYTES:
            raise RuntimeError("Corpus journal metadata budget exceeded")
    return used


def corpus_identity(spec: CorpusInput, *, verify_source: bool = False) -> dict:
    if not (spec.confirmed_local_research_use and spec.ranking.confirmed_local_research_use):
        raise PermissionError("Explicit local corpus research authorization required")
    paths = (
        spec.plan,
        spec.partitions,
        spec.reference_manifest,
        spec.ranking.original_vcf,
        spec.ranking.original_index,
        spec.ranking.phenotype_docx,
        spec.ranking.hpo_directory,
    )
    _private(*paths, spec.output)
    safe_path(spec.output, must_exist=False)
    for path in paths:
        safe_path(path)
        if path.is_relative_to(spec.output) or spec.output.is_relative_to(path):
            raise ValueError("Corpus output must be separate from its inputs")
    manifest = plan_identity(spec.plan)
    if manifest["code_sha256"] != code_checksum():
        raise ValueError("Corpus requires a fresh current-code source plan")
    materialization = read_json(spec.partitions / "run.json")
    journal = RunJournal.model_validate(materialization)
    expected = {
        "plan": str(spec.plan),
        "manifest_sha256": sha256(spec.plan / "manifest.json"),
        "source_sha256": manifest["source_sha256"],
        "code_sha256": code_checksum(),
        "scope": "partition preparation only",
    }
    if (
        journal.run_id != "coverage-materialization"
        or journal.configuration != expected
        or journal.configuration_hash
        != hashlib.sha256(json.dumps(expected, sort_keys=True).encode()).hexdigest()
    ):
        raise ValueError("Corpus requires fresh code-bound materialization")
    if verify_source and sha256(spec.ranking.original_vcf) != manifest["source_sha256"]:
        raise ValueError("Corpus source identity mismatch")
    if verify_source:
        for tool in (spec.normalization, spec.annotation):
            # Preserve the existing launcher symlink contract; resource bytes stay pinned.
            if (
                not tool.executable.is_absolute()
                or not os.access(tool.executable, os.X_OK)
                or sha256(tool.executable) != tool.executable_sha256
                or sha256(tool.job.reference_path) != tool.job.reference_sha256
                or (tool.job.provider == "vep" and not tool.job.cache_path.is_dir())
            ):
                raise ValueError("Pinned local tool, reference or cache mismatch")
    # Consume the complete bounded manifest, including its trailing checksum check.
    for ref in reference_records(spec):
        safe_path(ref.directory)
        _private(ref.directory)
        if ref.directory.is_relative_to(spec.output) or spec.output.is_relative_to(ref.directory):
            raise ValueError("Annotation references must be separate from corpus output")
    corpus_storage_bytes(spec)
    return {
        "version": VERSION,
        "specification": spec.model_dump(mode="json"),
        "code_sha256": code_checksum(),
        "plan_manifest_sha256": expected["manifest_sha256"],
        "materialization_journal_sha256": sha256(spec.partitions / "run.json"),
        "reference_manifest_sha256": spec.reference_manifest_sha256,
        "source_sha256": manifest["source_sha256"],
        "index_sha256": sha256(spec.ranking.original_index),
        "phenotype_sha256": sha256(spec.ranking.phenotype_docx),
        "hpo_manifest_sha256": sha256(spec.ranking.hpo_directory / "manifest.json"),
    }


def _index(spec: CorpusInput):
    path = spec.output / "index" / "references.sqlite"
    safe_path(path)
    return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)


def _verify_index(spec: CorpusInput):
    index = spec.output / "index"
    journal = RunJournal.model_validate(read_json(spec.output / "run.json"))
    record = journal.stages.get("index")
    if record is None or inventory(index) != record.outputs:
        raise ValueError("Corpus reference index changed")
    report = read_json(index / "manifest.json")
    if (
        report["references_index_sha256"] != sha256(index / "references.sqlite")
        or report["plan_manifest_sha256"] != sha256(spec.plan / "manifest.json")
        or report["materialization_journal_sha256"] != sha256(spec.partitions / "run.json")
        or report["reference_manifest_sha256"] != spec.reference_manifest_sha256
    ):
        raise ValueError("Corpus index binding mismatch")


def indexed_references(spec: CorpusInput) -> Iterator[tuple[CorpusReference, dict]]:
    with _index(spec) as db:
        for raw, segment in db.execute("SELECT reference,segment FROM refs ORDER BY first_row"):
            yield CorpusReference.model_validate_json(raw), json.loads(segment)


def index_references(spec: CorpusInput, output: Path):
    identity = corpus_identity(spec)
    manifest = plan_identity(spec.plan)
    materialization = RunJournal.model_validate(read_json(spec.partitions / "run.json"))
    schedule = CoverageInput(
        source=Path(manifest["source"]),
        source_sha256=manifest["source_sha256"],
        output=spec.plan,
        records_per_shard=manifest["records_per_shard"],
        shard_record_limits=manifest.get("shard_record_limits", {}),
        confirmed_local_research_use=True,
    )
    with sqlite3.connect(output / "references.sqlite") as db:
        db.execute("PRAGMA temp_store=MEMORY")
        db.execute(
            "CREATE TABLE refs (shard INTEGER PRIMARY KEY, directory TEXT UNIQUE, "
            "reference TEXT, first_row INTEGER UNIQUE, segment TEXT)"
        )
        count = 0
        for ref in reference_records(spec):
            count += 1
            db.execute(
                "INSERT INTO refs(shard,directory,reference) VALUES(?,?,?)",
                (ref.shard, str(ref.directory), ref.model_dump_json()),
            )
        for (directory,) in db.execute("SELECT directory FROM refs"):
            for parent in Path(directory).parents:
                if db.execute("SELECT 1 FROM refs WHERE directory=?", (str(parent),)).fetchone():
                    raise ValueError("Overlapping annotation reference directories")
        previous = shards = selected_records = selected_alleles = 0
        counts = {}
        with (spec.plan / "segments.jsonl").open("rb") as stream:
            while line := stream.readline(MAX_LINE_BYTES + 1):
                if len(line) > MAX_LINE_BYTES:
                    raise ValueError("Corpus source segment exceeds metadata budget")
                segment = _decode(line.decode("utf-8"))
                number = segment["shard"]
                if (
                    type(segment["first_source_row"]) is not int
                    or type(segment["last_source_row"]) is not int
                    or type(segment["source_records"]) is not int
                    or type(segment["alternate_alleles"]) is not int
                    or segment["alternate_alleles"] < 0
                    or segment["first_source_row"] != previous + 1
                    or segment["last_source_row"] - previous != segment["source_records"]
                    or segment["source_records"] < 1
                ):
                    raise ValueError("Corpus source intervals do not reconcile")
                previous = segment["last_source_row"]
                entry = counts.setdefault(
                    segment["disposition"], {"source_records": 0, "alternate_alleles": 0}
                )
                for key in entry:
                    entry[key] += segment[key]
                if number is not None:
                    shards += 1
                    if (
                        type(number) is not int
                        or number != shards
                        or segment["disposition"] != "eligible_autosomal_sequence"
                        or not 1 <= segment["source_records"] <= schedule.record_limit(number)
                    ):
                        raise ValueError("Corpus shard sequence does not reconcile")
                    if db.execute("SELECT 1 FROM refs WHERE shard=?", (number,)).fetchone():
                        record = materialization.stages.get(f"shard_{number:06d}")
                        partition = spec.partitions / f"shard_{number:06d}"
                        safe_path(partition)
                        if record is None or inventory(partition) != record.outputs:
                            raise ValueError("Materialized shard is absent or changed")
                        db.execute(
                            "UPDATE refs SET first_row=?,segment=? WHERE shard=?",
                            (segment["first_source_row"], json.dumps(segment), number),
                        )
                        selected_records += segment["source_records"]
                        selected_alleles += segment["alternate_alleles"]
        if (
            previous != manifest["source_records"]
            or counts != manifest["counts"]
            or sum(v["alternate_alleles"] for v in counts.values()) != manifest["alternate_alleles"]
            or shards != manifest["shards"]
            or db.execute("SELECT 1 FROM refs WHERE segment IS NULL LIMIT 1").fetchone()
        ):
            raise ValueError("Corpus full source ledger or selected references mismatch")
    if corpus_identity(spec) != identity:
        raise ValueError("Corpus inputs changed during indexing")
    bounded_json(
        output / "manifest.json",
        identity
        | {
            "reference_count": count,
            "referenced_bundle_bytes": reference_storage_bytes(spec),
            "references_index_sha256": sha256(output / "references.sqlite"),
            "source_plan": manifest,
            "coverage": {
                "included_source_records": selected_records,
                "included_alternate_alleles": selected_alleles,
                "unprocessed_source_records": manifest["source_records"] - selected_records,
                "unprocessed_alternate_alleles": manifest["alternate_alleles"] - selected_alleles,
                "source_dispositions": counts,
                "whole_genome_analysis_complete": False,
            },
        },
    )


def _verify_annotation(spec: CorpusInput, ref: CorpusReference, prepared) -> dict:
    safe_path(ref.directory)
    if sha256(ref.directory / "integrity_manifest.json") != ref.integrity_sha256:
        raise ValueError("Corpus annotation seal differs from explicit pin")
    for _ in _annotation_files(ref.directory):
        pass
    contents = verified(ref.directory)
    if set(contents) != ANNOTATION_FILES:
        raise ValueError("Unsupported corpus annotation inventory")
    manifest, _ = verify_partition(
        spec.plan, spec.partitions / f"shard_{ref.shard:06d}", prepared=prepared
    )
    prior = read_json(ref.directory / "run_manifest.json")
    if (
        prior.get("status") != "completed"
        or prior["source_sha256"] != manifest["source_sha256"]
        or contents["subset.vcf"]
        != sha256(spec.partitions / f"shard_{ref.shard:06d}" / "subset.vcf")
    ):
        raise ValueError("Corpus annotation source mismatch")
    for name, tool, incoming, outgoing in (
        ("normalization", spec.normalization, "subset.vcf", "normalized.vcf"),
        ("annotation", spec.annotation, "normalized.vcf", "annotated.vcf"),
    ):
        receipt = read_json(ref.directory / f"{name}.receipt.json")
        if (
            {
                k: v
                for k, v in receipt["configuration"].items()
                if k not in {"input_path", "output_path"}
            }
            != tool.job.model_dump(mode="json", exclude={"input_path", "output_path"})
            or receipt["executable_sha256"] != tool.executable_sha256
            or receipt["provider"] != tool.job.provider
            or receipt["returncode"] != 0
            or receipt["input_sha256"] != contents[incoming]
            or receipt["output_sha256"] != contents[outgoing]
            or receipt["reference_sha256"] != tool.job.reference_sha256
            or receipt["tool_version"] != tool.job.tool_version
            or receipt["data_version"] != tool.job.data_version
        ):
            raise ValueError("Corpus annotation tool or resource identity mismatch")
    return contents


def validate_references(spec: CorpusInput, output: Path):
    index = read_json(spec.output / "index" / "manifest.json")
    header = (spec.plan / "header.txt").read_text()
    used = (spec.output / "run.json").stat().st_size + 16 * 1024
    for ref, segment in indexed_references(spec):
        prepared = (index["source_plan"], segment, header)
        before = _verify_annotation(spec, ref, prepared)
        directory = output / f"shard_{ref.shard:06d}"
        directory.mkdir()
        validate_shard(
            spec.plan,
            spec.partitions / directory.name,
            ref.directory,
            directory,
            prepared_partition=prepared,
        )
        if _verify_annotation(spec, ref, prepared) != before:
            raise ValueError("Corpus annotation changed during validation")
        used = _journal_files_admission(directory, output, used)
        corpus_storage_bytes(spec, index["referenced_bundle_bytes"])


def _validation_paths(spec: CorpusInput) -> Iterator[Path]:
    for ref, _ in indexed_references(spec):
        yield spec.output / "validation" / f"shard_{ref.shard:06d}"


def assemble_corpus(spec: CorpusInput, output: Path):
    index = read_json(spec.output / "index" / "manifest.json")
    header = (spec.plan / "header.txt").read_text()
    common = None
    selected_records = selected_alleles = count = previous = 0
    with (output / "shard_proofs.jsonl").open("wb") as stream:
        written = 0
        for ref, segment in indexed_references(spec):
            _verify_annotation(spec, ref, (index["source_plan"], segment, header))
            path = spec.output / "validation" / f"shard_{ref.shard:06d}"
            safe_path(path)
            contents = verified(path)
            proof = read_json(path / "proof.json")
            current = {
                key: proof[key]
                for key in (
                    "version",
                    "code_sha256",
                    "source_sha256",
                    "plan_manifest_sha256",
                    "context",
                    "sample_identity_sha256",
                )
            }
            if common is None:
                common = current
            if (
                current != common
                or proof["version"] != ASSEMBLY_VERSION
                or proof["code_sha256"] != code_checksum()
                or proof["plan_manifest_sha256"] != index["plan_manifest_sha256"]
                or proof["source_sha256"] != index["source_sha256"]
                or proof["segment"] != segment
                or segment["first_source_row"] <= previous
            ):
                raise ValueError("Corpus validation identity or interval mismatch")
            previous = segment["last_source_row"]
            line = (
                json.dumps({"proof": proof, "validation_integrity": contents}, sort_keys=True)
                + "\n"
            ).encode()
            written += len(line)
            if len(line) > MAX_LINE_BYTES or written > MAX_MANIFEST_BYTES:
                raise ValueError("Corpus proof manifest exceeds metadata budget")
            stream.write(line)
            count += 1
            selected_records += segment["source_records"]
            selected_alleles += segment["alternate_alleles"]
    if common is None or count != index["reference_count"]:
        raise ValueError("Incomplete corpus validation")
    metrics = write_assembly_tables(lambda: _validation_paths(spec), output / "tables")
    if (
        metrics["alleles"] != selected_alleles
        or selected_records != index["coverage"]["included_source_records"]
        or selected_alleles != index["coverage"]["included_alternate_alleles"]
    ):
        raise ValueError("Corpus assembled coverage mismatch")
    # Recheck exact consumed validations and annotation seals after table streaming.
    with (output / "shard_proofs.jsonl").open("rb") as stream:
        for ref, segment in indexed_references(spec):
            item = _decode(stream.readline(MAX_LINE_BYTES + 1).decode("utf-8"))
            path = spec.output / "validation" / f"shard_{ref.shard:06d}"
            if (
                verified(path) != item["validation_integrity"]
                or read_json(path / "proof.json") != item["proof"]
                or _verify_annotation(spec, ref, (index["source_plan"], segment, header))
                != item["proof"]["annotation_integrity"]
            ):
                raise ValueError("Corpus inputs changed during assembly")
        if stream.read(1):
            raise ValueError("Unexpected corpus proof records")
    # Bind the complete streamed proofs, not a growing in-memory list in the root.
    bounded_json(
        output / "reconciliation.json",
        {
            "source_records": selected_records,
            "normalized_alleles": selected_alleles,
            "genotype_reconciliation": True,
        },
    )
    bounded_json(
        output / "run_manifest.json",
        {
            "format": CORPUS_ASSEMBLY_VERSION,
            "status": "completed",
            "code_sha256": code_checksum(),
            "source_sha256": index["source_sha256"],
            "context": common["context"],
            "source_plan": index["source_plan"],
            "plan_manifest_sha256": index["plan_manifest_sha256"],
            "proof_index": {
                "path": "shard_proofs.jsonl",
                "sha256": sha256(output / "shard_proofs.jsonl"),
                "shards": count,
            },
            "coverage": index["coverage"],
        },
    )
    seal(output)
    verify_assembly(output)


def run_stage(spec: CorpusInput, stage: str, output: Path):
    if not (spec.confirmed_local_research_use and spec.ranking.confirmed_local_research_use):
        raise PermissionError("Explicit corpus research authorization required")
    _private(spec.output, output)
    before = corpus_identity(spec)
    if stage != "index":
        _verify_index(spec)
    if stage == "index":
        index_references(spec, output)
    elif stage == "validation":
        validate_references(spec, output)
    elif stage == "assembly":
        assemble_corpus(spec, output)
    elif stage == "pair_preflight":
        directory = spec.output / "assembly"
        verify_assembly(directory)
        identity = sha256(directory / "integrity_manifest.json")
        with duckdb.connect(
            config={
                "memory_limit": "256MB",
                "threads": 1,
                "temp_directory": "",
                "autoinstall_known_extensions": False,
                "autoload_known_extensions": False,
            }
        ) as db:
            db.from_parquet(str(directory / "tables" / "variants.parquet")).create_view("variants")
            count = count_pair_work(db)
        verify_assembly(directory)
        if sha256(directory / "integrity_manifest.json") != identity:
            raise ValueError("Corpus assembly changed during preflight")
        bounded_json(
            output / "pair_workload.json",
            {
                "assembly_integrity_sha256": identity,
                "eligible_candidate_pairs": count,
                "pair_work_budget": PAIR_WORK_BUDGET,
                "within_budget": count <= PAIR_WORK_BUDGET,
                "scope": "all assembled candidates; global same-gene/chromosome enumeration",
                "retained_pair_count_assessed": False,
            },
        )
    elif stage == "ranking":
        check_pair_budget(spec)
        phase5_run(spec.ranking.model_copy(update={"output": output / "run"}))
    else:
        raise ValueError("Unknown fixed corpus stage")
    if stage != "index":
        _verify_index(spec)
    if corpus_identity(spec) != before:
        raise ValueError("Corpus inputs changed during stage")
    _journal_admission(spec, output)


def check_pair_budget(spec: CorpusInput):
    report = read_json(spec.output / "pair_preflight" / "pair_workload.json")
    if (
        report["assembly_integrity_sha256"]
        != sha256(spec.output / "assembly" / "integrity_manifest.json")
        or report["pair_work_budget"] != PAIR_WORK_BUDGET
        or type(report["eligible_candidate_pairs"]) is not int
        or report["eligible_candidate_pairs"] < 0
        or report["within_budget"] != (report["eligible_candidate_pairs"] <= PAIR_WORK_BUDGET)
    ):
        raise ValueError("Corpus pair-preflight identity mismatch")
    if report["within_budget"] is not True:
        raise CorpusPairBudgetExceeded(
            "Global pair budget blocks analysis; validated corpus is preserved"
        )


def supervise_stage(spec: CorpusInput, stage: str, output: Path):
    with _CoordinatorBudget(spec) as budget:
        _entry_resources(spec)
        referenced_bytes = reference_storage_bytes(spec)
        attempts = spec.output / "attempts"
        attempts.mkdir(mode=0o700, exist_ok=True)
        request = attempts / (uuid4().hex + ".request.json")
        bounded_json(
            request,
            {
                "specification": spec.model_dump(mode="json"),
                "stage": stage,
                "output": str(output),
                "code_sha256": code_checksum(),
            },
        )
        observation = {
            "stage": stage,
            "status": "failed",
            "peak_sampled_rss_bytes": 0,
            "peak_sampled_output_bytes": 0,
            "referenced_bundle_bytes": referenced_bytes,
            "process_returncode": None,
        }
        environment = {
            "PATH": str(Path(sys.executable).parent) + os.pathsep + os.defpath,
            "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
            "LANG": "C",
            "LANGSMITH_TRACING": "false",
            "LANGCHAIN_TRACING_V2": "false",
        }
        started = time.monotonic()
        try:
            budget.suspend()
            with subprocess.Popen(
                [sys.executable, "-m", "rare_disease_agent.workflows.corpus_worker", str(request)],
                env=environment,
                cwd=output,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            ) as process:
                try:
                    budget.worker_pid = process.pid
                    budget.resume()
                    while process.poll() is None:
                        memory = 0
                        with suppress(psutil.NoSuchProcess):
                            parent = psutil.Process(process.pid)
                            for item in [parent, *parent.children(recursive=True)]:
                                with suppress(psutil.NoSuchProcess):
                                    memory += item.memory_info().rss
                        size = corpus_storage_bytes(spec, referenced_bytes, live=True)
                        observation["peak_sampled_rss_bytes"] = max(
                            observation["peak_sampled_rss_bytes"], memory
                        )
                        observation["peak_sampled_output_bytes"] = max(
                            observation["peak_sampled_output_bytes"], size
                        )
                        if (
                            memory > 3 * 1024**3
                            or time.monotonic() - started > 3600
                            or psutil.virtual_memory().available < 256 * 1024**2
                            or psutil.disk_usage(spec.output).free < 25 * 1024**3
                        ):
                            raise RuntimeError("Corpus stage resource boundary reached")
                        time.sleep(0.25)
                    observation["process_returncode"] = process.returncode
                    if process.returncode:
                        raise RuntimeError("Corpus worker failed")
                    observation["peak_sampled_output_bytes"] = max(
                        observation["peak_sampled_output_bytes"],
                        corpus_storage_bytes(spec, referenced_bytes),
                    )
                    observation["status"] = "completed"
                except BaseException:
                    with suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                    raise
        finally:
            observation["runtime_seconds"] = round(time.monotonic() - started, 3)
            bounded_json(request.with_suffix(".measurement.json"), observation)


def run_corpus(spec: CorpusInput):
    with _CoordinatorBudget(spec) as budget:
        _entry_resources(spec)
        identity = corpus_identity(spec, verify_source=True)
        if (
            len((json.dumps(identity, indent=2, sort_keys=True) + "\n").encode()) + 16 * 1024
            > MAX_ROOT_BYTES
        ):
            raise ValueError("Corpus root configuration exceeds metadata budget")
        spec.output.mkdir(mode=0o700, exist_ok=True)
        journal = spec.output / "run.json"
        if journal.exists():
            safe_path(journal)
            if journal.stat().st_size > MAX_ROOT_BYTES:
                raise ValueError("Corpus root journal exceeds metadata budget")
        with (spec.output / ".run.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            budget.resume()
            runner = RestartableRun(spec.output, run_id=spec.ranking.run_id, configuration=identity)
            for stage in ("index", "validation", "assembly", "pair_preflight", "ranking"):
                budget.resume()
                if corpus_identity(spec) != identity:
                    raise ValueError("Corpus identity changed")
                if stage == "ranking":
                    check_pair_budget(spec)

                def execute(output, stage=stage):
                    budget.suspend()
                    try:
                        supervise_stage(spec, stage, output)
                    finally:
                        budget.resume()
                    _journal_admission(spec, output)
                    corpus_storage_bytes(spec)

                runner.stage(stage, execute)
                corpus_storage_bytes(spec)
            budget.resume()
            if corpus_identity(spec, verify_source=True) != identity:
                raise ValueError("Corpus inputs changed during execution")
            index = read_json(spec.output / "index" / "manifest.json")
            header = (spec.plan / "header.txt").read_text()
            for ref, segment in indexed_references(spec):
                _verify_annotation(spec, ref, (index["source_plan"], segment, header))
            runner.complete()
            corpus_storage_bytes(spec)
