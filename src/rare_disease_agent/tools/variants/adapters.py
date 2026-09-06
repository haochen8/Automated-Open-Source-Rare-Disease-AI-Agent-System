"""Provider-neutral, preview-first contracts for local established annotation tools."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import Literal, Protocol

import psutil
from pydantic import BaseModel, ConfigDict, Field

from rare_disease_agent.resource_management import sha256


class ToolJob(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider: Literal["bcftools", "vep"]
    tool_version: str = Field(pattern=r"^\d+(?:\.\d+){0,2}$")
    data_version: str = Field(pattern=r"^[0-9][0-9A-Za-z_.-]*$")
    genome_build: Literal["GRCh37", "GRCh38"]
    input_path: Path
    output_path: Path
    reference_path: Path
    reference_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    cache_path: Path | None = None
    threads: int = Field(default=2, ge=1, le=4)
    timeout_seconds: int = Field(default=300, ge=1, le=3600)
    memory_mb: int = Field(default=2048, ge=128, le=8192)
    output_limit_mb: int = Field(default=1024, ge=1, le=8192)

    def preview(self) -> list[str]:
        source, output, reference = (
            str(path.resolve()) for path in (self.input_path, self.output_path, self.reference_path)
        )
        if source == output:
            raise ValueError("Annotation cannot overwrite input")
        if self.provider == "bcftools":
            return [
                "bcftools",
                "norm",
                "--check-ref",
                "e",
                "--fasta-ref",
                reference,
                "--multiallelics",
                "-any",
                "--threads",
                str(self.threads),
                "--output-type",
                "v",
                "--output",
                output,
                source,
            ]
        if self.cache_path is None or self.data_version != self.tool_version.split(".")[0]:
            raise ValueError("VEP requires a matching pinned local cache version")
        return [
            "vep",
            "--offline",
            "--cache",
            "--cache_version",
            self.data_version,
            "--dir_cache",
            str(self.cache_path.resolve()),
            "--assembly",
            self.genome_build,
            "--fasta",
            reference,
            "--fork",
            str(self.threads),
            "--buffer_size",
            "500",
            "--no_stats",
            "--vcf",
            "--input_file",
            source,
            "--output_file",
            output,
        ]


class ProcessRunner(Protocol):
    def __call__(self, command: list[str], job: ToolJob) -> int: ...


def bounded_process(command: list[str], job: ToolJob) -> int:
    """Local child-process supervisor; no shell, network flags or captured patient output."""
    with subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env={
            "PATH": os.defpath,
            "LANG": "C",
            "LANGSMITH_TRACING": "false",
            "LANGCHAIN_TRACING_V2": "false",
        },
    ) as process:
        start = time.monotonic()
        try:
            while process.poll() is None:
                parent = psutil.Process(process.pid)
                children = parent.children(recursive=True)
                memory = sum(
                    item.memory_info().rss for item in [parent, *children] if item.is_running()
                )
                size = job.output_path.stat().st_size if job.output_path.exists() else 0
                if (
                    time.monotonic() - start > job.timeout_seconds
                    or memory > job.memory_mb * 1024**2
                    or size > job.output_limit_mb * 1024**2
                ):
                    raise RuntimeError("Annotation resource limit exceeded")
                time.sleep(0.05)
            return process.returncode
        except BaseException:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise


def execute_job(
    job: ToolJob,
    *,
    authorized: bool = False,
    runner: ProcessRunner = bounded_process,
    observed_tool_version: str | None = None,
) -> dict:
    command = job.preview()
    if not authorized:
        raise PermissionError("Explicit local annotation execution authorization required")
    executable_checksum = None
    if runner is bounded_process:
        resolved = shutil.which(command[0])
        if resolved is None:
            raise FileNotFoundError("Pinned annotation tool is not installed")
        command[0] = str(Path(resolved).resolve())
        executable_checksum = sha256(Path(command[0]))
        # Independently observe the executable for real execution.
        observed = subprocess.run(
            [command[0], "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
            env={"PATH": os.defpath, "LANG": "C"},
        )
        import re

        match = re.search(
            r"(?:bcftools |(?:ensembl-)?vep[^0-9]*)([0-9]+(?:\.[0-9]+)*)", observed.stdout, re.I
        )
        observed_tool_version = match.group(1) if match else None
    if observed_tool_version != job.tool_version:
        raise ValueError("Observed tool version must match pinned version")
    if not job.input_path.is_file() or sha256(job.reference_path) != job.reference_sha256:
        raise ValueError("Missing input or reference checksum mismatch")
    if job.output_path.exists():
        raise ValueError("Output already exists")
    if job.provider == "vep" and not job.cache_path.is_dir():
        raise ValueError("Local VEP cache missing")
    if (
        psutil.virtual_memory().available < (job.memory_mb + 512) * 1024**2
        or psutil.disk_usage(job.output_path.parent).free < job.output_limit_mb * 1024**2
    ):
        raise RuntimeError("Insufficient live memory or disk")
    started = time.time()
    try:
        code = runner(command, job)
        if code != 0 or not job.output_path.is_file() or job.output_path.stat().st_size == 0:
            raise RuntimeError("Annotation failed or produced no output")
        if job.output_path.stat().st_size > job.output_limit_mb * 1024**2:
            raise RuntimeError("Annotation output limit exceeded")
        return {
            "provider": job.provider,
            "tool_version": job.tool_version,
            "data_version": job.data_version,
            "reference_sha256": job.reference_sha256,
            "executable_sha256": executable_checksum,
            "command_preview": command,
            "input_sha256": sha256(job.input_path),
            "output_sha256": sha256(job.output_path),
            "started_at": started,
            "ended_at": time.time(),
            "configuration": job.model_dump(mode="json"),
            "returncode": code,
        }
    except BaseException:
        job.output_path.unlink(missing_ok=True)
        raise
