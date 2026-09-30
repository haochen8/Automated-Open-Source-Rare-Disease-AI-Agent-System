"""Serial, process-isolated offline annotation, validation, assembly and global ranking."""

from __future__ import annotations

import fcntl
import os
import shutil
import signal
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

import duckdb
import psutil
from pydantic import BaseModel, ConfigDict, Field, model_validator

from rare_disease_agent.resource_management import atomic_json, sha256
from rare_disease_agent.tools.variants.adapters import ToolJob, execute_job
from rare_disease_agent.tools.variants.pair_workload import PAIR_WORK_BUDGET, count_pair_work
from rare_disease_agent.workflows.assembly import (
    assemble_verified,
    read_json,
    seal,
    validate_shard,
    verified,
    verify_assembly,
    verify_partition,
)
from rare_disease_agent.workflows.partitioning import _private
from rare_disease_agent.workflows.phase5 import Phase5Input, code_checksum, phase5_run
from rare_disease_agent.workflows.recovery import RestartableRun


class PinnedTool(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    executable: Path
    executable_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    job: ToolJob  # Input/output paths are templates, replaced only by fixed stage-owned paths.


class ReusedAnnotation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    directory: Path
    integrity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class CoverageBatchInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    plan: Path
    partitions: Path
    shards: list[int] = Field(min_length=1, max_length=8)
    normalization: PinnedTool
    annotation: PinnedTool
    ranking: Phase5Input
    output: Path
    confirmed_local_research_use: bool
    confirmed_annotation_execution: bool
    reuse_annotations: dict[int, ReusedAnnotation] = Field(default_factory=dict)

    @model_validator(mode="after")
    def bounded_scope(self):
        absolute_paths = (
            self.plan,
            self.partitions,
            self.output,
            self.ranking.original_vcf,
            self.ranking.original_index,
            self.ranking.phenotype_docx,
            self.ranking.hpo_directory,
            self.ranking.annotation_rehearsal,
            self.ranking.output,
            self.normalization.executable,
            self.annotation.executable,
            self.normalization.job.reference_path,
            self.annotation.job.reference_path,
            self.annotation.job.cache_path,
        )
        if any(path is None or not path.is_absolute() for path in absolute_paths):
            raise ValueError("Batch inputs and outputs require absolute local paths")
        if not self.reuse_annotations.keys() <= set(self.shards) or any(
            not item.directory.is_absolute() for item in self.reuse_annotations.values()
        ):
            raise ValueError("Annotation reuse must name absolute paths for requested shards")
        if (
            self.shards != sorted(set(self.shards))
            or any(type(x) is not int or not 1 <= x <= 100_000 for x in self.shards)
            or self.normalization.job.provider != "bcftools"
            or self.annotation.job.provider != "vep"
            or self.annotation.job.data_version != "116"
            or self.annotation.job.annotation_profile != "track1"
        ):
            raise ValueError("Unsupported batch scope or annotation profile")
        for tool in (self.normalization, self.annotation):
            job = tool.job
            if (
                job.buffer_size != 1
                or job.threads != 1
                or job.memory_mb > 320
                or job.output_limit_mb > 512
                or job.timeout_seconds > 1800
                or job.genome_build != "GRCh38"
            ):
                raise ValueError("Batch annotation exceeds pilot resource contract")
        if (
            self.normalization.job.reference_sha256 != self.annotation.job.reference_sha256
            or self.normalization.job.reference_path != self.annotation.job.reference_path
            or self.ranking.annotation_rehearsal.resolve() != (self.output / "assembly").resolve()
            or self.ranking.output.resolve() != (self.output / "ranking" / "run").resolve()
        ):
            raise ValueError("Batch paths or references do not reconcile")
        return self


def check_authorization(spec: CoverageBatchInput):
    if not (
        spec.confirmed_local_research_use
        and spec.ranking.confirmed_local_research_use
        and (spec.confirmed_annotation_execution or set(spec.reuse_annotations) == set(spec.shards))
    ):
        raise PermissionError(
            "Explicit local research and any new annotation authorization required"
        )


def reuse_identity(spec: CoverageBatchInput, number: int) -> dict:
    """Pin source bytes and full tool configuration; never adopt old ingestion or ranking."""
    item = spec.reuse_annotations[number]
    source = item.directory
    _private(source)
    if (
        source.is_symlink()
        or source.resolve().is_relative_to(spec.output.resolve())
        or spec.output.resolve().is_relative_to(source.resolve())
    ):
        raise ValueError("Reused annotation must be separate from batch output")
    manifest, _ = verify_partition(spec.plan, spec.partitions / f"shard_{number:06d}")
    if sha256(source / "integrity_manifest.json") != item.integrity_sha256:
        raise ValueError("Reused annotation seal differs from explicit pin")
    contents = verified(source)
    required = {
        "subset.vcf",
        "normalized.vcf",
        "annotated.vcf",
        "run_manifest.json",
        "normalization.receipt.json",
        "annotation.receipt.json",
    }
    if set(contents) != required:
        raise ValueError("Unsupported reused annotation inventory")
    prior = read_json(source / "run_manifest.json")
    if (
        prior.get("status") != "completed"
        or prior.get("source_sha256") != manifest["source_sha256"]
        or contents["subset.vcf"] != sha256(spec.partitions / f"shard_{number:06d}" / "subset.vcf")
    ):
        raise ValueError("Reused annotation source mismatch")
    for name, tool, incoming, outgoing in (
        ("normalization", spec.normalization, "subset.vcf", "normalized.vcf"),
        ("annotation", spec.annotation, "normalized.vcf", "annotated.vcf"),
    ):
        receipt = read_json(source / (name + ".receipt.json"))
        configuration = receipt["configuration"]
        if (
            {k: v for k, v in configuration.items() if k not in {"input_path", "output_path"}}
            != tool.job.model_dump(mode="json", exclude={"input_path", "output_path"})
            or receipt["provider"] != tool.job.provider
            or receipt["executable_sha256"] != tool.executable_sha256
            or receipt["returncode"] != 0
            or receipt["input_sha256"] != contents[incoming]
            or receipt["output_sha256"] != contents[outgoing]
            or receipt["reference_sha256"] != tool.job.reference_sha256
            or receipt["tool_version"] != tool.job.tool_version
            or receipt["data_version"] != tool.job.data_version
        ):
            raise ValueError("Reused annotation tool or receipt mismatch")
    return contents


def import_annotation(spec: CoverageBatchInput, number: int, output: Path):
    before = reuse_identity(spec, number)
    source = spec.reuse_annotations[number].directory
    for name in [*before, "integrity_manifest.json"]:
        shutil.copyfile(source / name, output / name)
    if verified(output) != before or reuse_identity(spec, number) != before:
        raise ValueError("Reused annotation changed while copying")
    if (
        sha256(output / "integrity_manifest.json")
        != spec.reuse_annotations[number].integrity_sha256
    ):
        raise ValueError("Reused annotation seal changed while copying")


def pair_preflight(spec: CoverageBatchInput, output: Path):
    directory = spec.output / "assembly"
    verify_assembly(directory)
    before = sha256(directory / "integrity_manifest.json")
    with duckdb.connect(config={"memory_limit": "256MB", "threads": 1, "temp_directory": ""}) as db:
        db.from_parquet(str(directory / "tables" / "variants.parquet")).create_view("variants")
        count = count_pair_work(db)
    verify_assembly(directory)
    if sha256(directory / "integrity_manifest.json") != before:
        raise ValueError("Assembly changed during pair preflight")
    atomic_json(
        output / "pair_workload.json",
        {
            "assembly_integrity_sha256": before,
            "eligible_candidate_pairs": count,
            "pair_work_budget": PAIR_WORK_BUDGET,
            "within_budget": count <= PAIR_WORK_BUDGET,
            "scope": "all assembled candidates; global same-gene/chromosome enumeration",
            "retained_pair_count_assessed": False,
        },
    )


def batch_identity(spec: CoverageBatchInput) -> dict:
    check_authorization(spec)
    paths = (
        spec.plan,
        spec.partitions,
        spec.output,
        spec.ranking.original_vcf,
        spec.ranking.original_index,
        spec.ranking.phenotype_docx,
        spec.ranking.hpo_directory,
    )
    _private(*paths)
    for path in paths:
        if path != spec.output and (
            path.resolve().is_relative_to(spec.output.resolve())
            or spec.output.resolve().is_relative_to(path.resolve())
        ):
            raise ValueError("Batch output must be separate from every input")
    selected = []
    manifest = None
    for number in spec.shards:
        shard = spec.partitions / f"shard_{number:06d}"
        manifest, segment = verify_partition(spec.plan, shard)
        if segment["shard"] != number:
            raise ValueError("Requested shard identity mismatch")
        selected.append({"segment": segment, "subset_sha256": sha256(shard / "subset.vcf")})
    if sha256(spec.ranking.original_vcf) != manifest["source_sha256"]:
        raise ValueError("Ranking source does not match the coverage plan")
    for tool in (spec.normalization, spec.annotation):
        # Preserve launcher symlinks for the existing tool environment, but pin their bytes.
        if (
            not tool.executable.is_absolute()
            or not os.access(tool.executable, os.X_OK)
            or sha256(tool.executable) != tool.executable_sha256
            or sha256(tool.job.reference_path) != tool.job.reference_sha256
        ):
            raise ValueError("Pinned local tool or reference mismatch")
        if tool.job.provider == "vep" and not tool.job.cache_path.is_dir():
            raise ValueError("Existing local annotation cache required")
    return {
        "specification": spec.model_dump(mode="json"),
        "code_sha256": code_checksum(),
        "plan_manifest_sha256": sha256(spec.plan / "manifest.json"),
        "shards": selected,
        "source_sha256": manifest["source_sha256"],
        "phenotype_sha256": sha256(spec.ranking.phenotype_docx),
        "index_sha256": sha256(spec.ranking.original_index),
        "hpo_manifest_sha256": sha256(spec.ranking.hpo_directory / "manifest.json"),
        "reused_annotations": {str(n): reuse_identity(spec, n) for n in spec.reuse_annotations},
    }


def annotate_shard(spec: CoverageBatchInput, number: int, output: Path):
    manifest, _ = verify_partition(spec.plan, spec.partitions / f"shard_{number:06d}")
    shutil.copyfile(spec.partitions / f"shard_{number:06d}" / "subset.vcf", output / "subset.vcf")
    operations = []
    for name, tool, source, target in (
        ("normalization", spec.normalization, "subset.vcf", "normalized.vcf"),
        ("annotation", spec.annotation, "normalized.vcf", "annotated.vcf"),
    ):
        if sha256(tool.executable) != tool.executable_sha256:
            raise ValueError("Annotation executable changed")
        job = tool.job.model_copy(
            update={"input_path": output / source, "output_path": output / target}
        )
        previous_path = os.environ.get("PATH")
        try:
            os.environ["PATH"] = str(tool.executable.parent) + os.pathsep + os.defpath
            if shutil.which(job.provider) != str(tool.executable):
                raise ValueError("Tool launcher does not match pinned executable")
            observation = {}
            receipt = execute_job(
                job, authorized=True, observation=observation, isolate_process_group=False
            )
        finally:
            if previous_path is None:
                os.environ.pop("PATH", None)
            else:
                os.environ["PATH"] = previous_path
        if receipt["executable_sha256"] != tool.executable_sha256:
            raise ValueError("Executed tool checksum mismatch")
        atomic_json(output / (name + ".receipt.json"), receipt)
        operations.append(job.model_dump(mode="json"))
    atomic_json(
        output / "run_manifest.json",
        {
            "status": "completed",
            "source_sha256": manifest["source_sha256"],
            "authorized_scope": {"operations": operations},
            "whole_genome_analysis_complete": False,
        },
    )
    seal(output)


def run_stage(spec: CoverageBatchInput, stage: str, output: Path):
    """Fixed dispatcher; accepts neither arbitrary commands nor SQL."""
    check_authorization(spec)
    _private(spec.plan, spec.partitions, spec.output, output)
    if stage == "assembly":
        assemble_verified(
            spec.plan, [spec.output / f"validation_{n:06d}" for n in spec.shards], output
        )
    elif stage == "pair_preflight":
        pair_preflight(spec, output)
    elif stage == "ranking":
        report = read_json(spec.output / "pair_preflight" / "pair_workload.json")
        if (
            report["assembly_integrity_sha256"]
            != sha256(spec.output / "assembly" / "integrity_manifest.json")
            or report["pair_work_budget"] != PAIR_WORK_BUDGET
            or report["within_budget"] is not True
            or not 0 <= report["eligible_candidate_pairs"] <= PAIR_WORK_BUDGET
        ):
            raise ValueError("Global pair preflight blocks ranking; preserve all candidates")
        ranking = spec.ranking.model_copy(update={"output": output / "run"})
        phase5_run(ranking)
    else:
        allowed = {
            f"{prefix}_{n:06d}": (prefix, n)
            for prefix in ("annotation", "validation")
            for n in spec.shards
        }
        if stage not in allowed:
            raise ValueError("Unknown fixed batch stage")
        prefix, number = allowed[stage]
        if prefix == "annotation":
            if number in spec.reuse_annotations:
                import_annotation(spec, number, output)
            else:
                annotate_shard(spec, number, output)
        else:
            validate_shard(
                spec.plan,
                spec.partitions / f"shard_{number:06d}",
                spec.output / f"annotation_{number:06d}",
                output,
            )


def output_size(directory: Path) -> int:
    size = 0
    for path in directory.rglob("*"):
        # Publication can rename an entire subtree during a measurement.
        with suppress(FileNotFoundError):
            if path.is_file():
                size += path.stat().st_size
    return size


def supervise_stage(spec: CoverageBatchInput, stage: str, output: Path):
    """Checkpoint each attempt, including failed attempts, before propagating failure."""
    attempts = spec.output / "attempts"
    attempts.mkdir(mode=0o700, exist_ok=True)
    identity = uuid4().hex
    request = attempts / (identity + ".request.json")
    atomic_json(
        request,
        {
            "specification": spec.model_dump(mode="json"),
            "stage": stage,
            "output": str(output),
            "code_sha256": code_checksum(),
        },
    )
    environment = {
        "PATH": str(Path(sys.executable).parent) + os.pathsep + os.defpath,
        "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
        "LANG": "C",
        "LANGSMITH_TRACING": "false",
        "LANGCHAIN_TRACING_V2": "false",
    }
    started = time.monotonic()
    observation = {
        "stage": stage,
        "status": "failed",
        "peak_sampled_rss_bytes": 0,
        "peak_sampled_output_bytes": 0,
        "process_returncode": None,
    }
    try:
        if (
            psutil.virtual_memory().available < 1024**3
            or psutil.disk_usage(spec.output).free < 25 * 1024**3
        ):
            raise RuntimeError("Insufficient live resources for stage")
        with subprocess.Popen(
            [sys.executable, "-m", "rare_disease_agent.workflows.coverage_worker", str(request)],
            env=environment,
            cwd=output,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        ) as process:
            try:
                while process.poll() is None:
                    memory = 0
                    with suppress(psutil.NoSuchProcess):
                        parent = psutil.Process(process.pid)
                        for item in [parent, *parent.children(recursive=True)]:
                            with suppress(psutil.NoSuchProcess):
                                memory += item.memory_info().rss
                    size = output_size(spec.output)
                    observation["peak_sampled_rss_bytes"] = max(
                        observation["peak_sampled_rss_bytes"], memory
                    )
                    observation["peak_sampled_output_bytes"] = max(
                        observation["peak_sampled_output_bytes"], size
                    )
                    if (
                        memory > 3 * 1024**3
                        or size > 2 * 1024**3
                        or time.monotonic() - started > 3600
                        or psutil.virtual_memory().available < 256 * 1024**2
                        or psutil.disk_usage(spec.output).free < 25 * 1024**3
                    ):
                        raise RuntimeError("Batch stage resource boundary reached")
                    time.sleep(0.25)
                observation["process_returncode"] = process.returncode
                if process.returncode:
                    raise RuntimeError("Private batch worker failed")
                size = output_size(spec.output)
                observation["peak_sampled_output_bytes"] = max(
                    observation["peak_sampled_output_bytes"], size
                )
                if size > 2 * 1024**3:
                    raise RuntimeError("Batch output boundary reached")
                observation["status"] = "completed"
            except BaseException:
                # All nested tools inherit this group. Kill it even if the leader exited,
                # and even when process inspection is denied: no child inventory is needed.
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                raise
    finally:
        observation["runtime_seconds"] = round(time.monotonic() - started, 3)
        atomic_json(attempts / (identity + ".measurement.json"), observation)


def run_batch(spec: CoverageBatchInput):
    configuration = batch_identity(spec)
    if (
        psutil.virtual_memory().available < 1024**3
        or psutil.disk_usage(spec.output.parent).free < 25 * 1024**3
    ):
        raise RuntimeError("Insufficient live resources for batch")
    spec.output.mkdir(mode=0o700, exist_ok=True)
    with (spec.output / ".run.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        runner = RestartableRun(
            spec.output, run_id=spec.ranking.run_id, configuration=configuration
        )
        stages = [
            f"{prefix}_{n:06d}" for n in spec.shards for prefix in ("annotation", "validation")
        ]
        for stage in [*stages, "assembly", "pair_preflight", "ranking"]:
            if code_checksum() != configuration["code_sha256"]:
                raise ValueError("Implementation changed during batch")
            runner.stage(stage, lambda output, stage=stage: supervise_stage(spec, stage, output))
        if batch_identity(spec) != configuration:
            raise ValueError("Batch inputs changed during execution")
        runner.complete()
