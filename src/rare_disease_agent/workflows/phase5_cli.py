"""Reviewed Phase 5 plan and supervised offline run commands."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

import psutil
import typer

from rare_disease_agent.resource_management import atomic_json, sha256
from rare_disease_agent.workflows.authorized import _outside_git
from rare_disease_agent.workflows.benchmark import select_autosomal_benchmark
from rare_disease_agent.workflows.phase5 import Phase5Input
from rare_disease_agent.workflows.phase5_diagnostics import (
    WORKER_IDENTITY_MISMATCH,
    WORKER_STAGE_INTEGRITY,
    Phase5IdentityMismatch,
    Phase5StageIntegrityError,
)

app = typer.Typer(help="Private bounded Track 1 rehearsal; no downloads or live models.")

PROCESS_INSPECTION_DENIED = (
    "Host process inspection permission denied; cannot enforce Phase 5 resource limits. "
    "Run stopped. Process inspection permission is required to retry."
)


class ProcessInspectionDenied(RuntimeError):
    """The supervisor cannot enforce limits because process inspection was denied."""


def load_spec(path: Path) -> Phase5Input:
    _outside_git(path.resolve())
    return Phase5Input.model_validate_json(path.read_text())


@app.command("benchmark-select")
def benchmark_select(path: Path, per_autosome: int = 100):
    """Select a private autosomal sample; destination must be a fresh rehearsal directory."""
    try:
        spec = load_spec(path)
        if not spec.confirmed_local_research_use:
            raise PermissionError("Local research authorization required")
        result = select_autosomal_benchmark(
            spec.original_vcf, spec.annotation_rehearsal, per_autosome=per_autosome
        )
        typer.echo(json.dumps(result))
    except Exception as exc:
        typer.echo("Private benchmark selection failed: " + type(exc).__name__, err=True)
        raise typer.Exit(code=1) from None


@app.command("plan")
def plan(path: Path):
    spec = load_spec(path)
    typer.echo(
        json.dumps(
            {
                "mode": "bounded offline end-to-end rehearsal",
                "input": "previously verified annotation subset and explicit phenotype IDs",
                "run_id": spec.run_id,
                "configuration_sha256": sha256(path),
                "stages": [
                    "verify existing annotation",
                    "ingest all consequences",
                    "phenotype and inheritance",
                    "six deterministic strategies",
                    "private reports and integrity verification",
                ],
                "new_annotation": False,
                "downloads": False,
                "live_model": False,
                "memory_limit_bytes": 3 * 1024**3,
                "output_limit_bytes": 2 * 1024**3,
                "minimum_free_disk_bytes": 25 * 1024**3,
                "timeout_seconds": 3600,
                "affected_sample_confirmed": spec.confirmed_affected_sample,
            }
        )
    )


def supervise(path: Path) -> dict:
    spec = load_spec(path)
    _outside_git(spec.output.resolve())
    spec.output.mkdir(parents=True, exist_ok=True, mode=0o700)
    root = Path(__file__).resolve().parents[2]
    invocation_id = uuid4().hex
    receipt_path = spec.output.parent / ("phase5-" + invocation_id + "-failure.json")
    command = [
        sys.executable,
        "-m",
        "rare_disease_agent.workflows.phase5_worker",
        str(path.resolve()),
        invocation_id,
    ]
    environment = {
        "PATH": str(Path(sys.executable).parent) + os.pathsep + os.defpath,
        "PYTHONPATH": str(root),
        "LANG": "C",
        "LANGSMITH_TRACING": "false",
        "LANGCHAIN_TRACING_V2": "false",
    }
    started = time.monotonic()
    peak_rss = peak_disk = 0
    with subprocess.Popen(
        command,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    ) as process:
        try:
            while process.poll() is None:
                memory = 0
                try:
                    with suppress(psutil.NoSuchProcess):
                        parent = psutil.Process(process.pid)
                        for item in [parent, *parent.children(recursive=True)]:
                            with suppress(psutil.NoSuchProcess):
                                memory += item.memory_info().rss
                except (psutil.AccessDenied, PermissionError):
                    raise ProcessInspectionDenied(PROCESS_INSPECTION_DENIED) from None
                disk = 0
                for entry in spec.output.rglob("*"):
                    # Atomic stage publication can rename files during a measurement.
                    with suppress(FileNotFoundError):
                        if entry.is_file():
                            disk += entry.stat().st_size
                peak_rss, peak_disk = max(peak_rss, memory), max(peak_disk, disk)
                if (
                    memory > 3 * 1024**3
                    or disk > 2 * 1024**3
                    or time.monotonic() - started > 3600
                    or psutil.disk_usage(spec.output).free < 25 * 1024**3
                    or psutil.virtual_memory().available < 256 * 1024**2
                ):
                    raise RuntimeError("Private rehearsal resource boundary reached")
                time.sleep(0.25)
            if process.returncode in {WORKER_IDENTITY_MISMATCH, WORKER_STAGE_INTEGRITY}:
                diagnostic = {}
                # Bind the receipt to this invocation, including when sibling runs share an ID.
                # Read a bounded receipt and render only known codes, never its exception text.
                with suppress(OSError, ValueError, TypeError):
                    with receipt_path.open() as receipt:
                        payload = json.loads(receipt.read(65537))
                    if isinstance(payload, dict) and payload.get("invocation_id") == invocation_id:
                        diagnostic = payload
                if process.returncode == WORKER_IDENTITY_MISMATCH:
                    raise Phase5IdentityMismatch(diagnostic.get("identity_mismatch"))
                stage_error = diagnostic.get("stage_integrity")
                if not isinstance(stage_error, dict):
                    stage_error = {}
                raise Phase5StageIntegrityError(stage_error.get("stage"), stage_error.get("reason"))
            if process.returncode:
                raise RuntimeError("Private rehearsal failed; inspect sanitized local stage status")
        except BaseException:
            if process.poll() is None:
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise
    measurement = {
        "runtime_seconds": round(time.monotonic() - started, 2),
        "peak_sampled_rss_bytes": peak_rss,
        "peak_sampled_output_bytes": peak_disk,
        "process_returncode": 0,
        "configuration_sha256": sha256(path),
    }
    # Keep measurements outside immutable stage directories and preserve earlier observations.
    receipt = spec.output / ("supervision-" + str(time.time_ns()) + ".json")
    atomic_json(receipt, measurement)
    return measurement


@app.command("run")
def run(path: Path):
    try:
        typer.echo(json.dumps(supervise(path)))
    except Phase5IdentityMismatch as exc:
        typer.echo(str(Phase5IdentityMismatch(exc.codes)), err=True)
        raise typer.Exit(code=1) from None
    except Phase5StageIntegrityError as exc:
        typer.echo(str(Phase5StageIntegrityError(exc.stage, exc.reason)), err=True)
        raise typer.Exit(code=1) from None
    except ProcessInspectionDenied:
        # Only a fixed diagnostic is safe to expose; exception details can contain private data.
        typer.echo("Private Phase 5 failed: " + PROCESS_INSPECTION_DENIED, err=True)
        raise typer.Exit(code=1) from None
    except Exception as exc:
        typer.echo("Private Phase 5 failed: " + type(exc).__name__, err=True)
        raise typer.Exit(code=1) from None
