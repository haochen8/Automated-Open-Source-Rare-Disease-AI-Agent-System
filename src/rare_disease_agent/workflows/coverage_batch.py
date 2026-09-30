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

import psutil
from pydantic import BaseModel, ConfigDict, Field, model_validator

from rare_disease_agent.resource_management import atomic_json, sha256
from rare_disease_agent.tools.variants.adapters import ToolJob, execute_job
from rare_disease_agent.workflows.assembly import (
    assemble_verified,
    seal,
    validate_shard,
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


def batch_identity(spec: CoverageBatchInput) -> dict:
    if not (
        spec.confirmed_local_research_use
        and spec.confirmed_annotation_execution
        and spec.ranking.confirmed_local_research_use
    ):
        raise PermissionError(
            "Explicit bounded annotation and local research authorization required"
        )
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
    if not (spec.confirmed_annotation_execution and spec.confirmed_local_research_use):
        raise PermissionError("Explicit batch authorization required")
    _private(spec.plan, spec.partitions, spec.output, output)
    if stage == "assembly":
        assemble_verified(
            spec.plan, [spec.output / f"validation_{n:06d}" for n in spec.shards], output
        )
    elif stage == "ranking":
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
        for stage in [*stages, "assembly", "ranking"]:
            if code_checksum() != configuration["code_sha256"]:
                raise ValueError("Implementation changed during batch")
            runner.stage(stage, lambda output, stage=stage: supervise_stage(spec, stage, output))
        if batch_identity(spec) != configuration:
            raise ValueError("Batch inputs changed during execution")
        runner.complete()
