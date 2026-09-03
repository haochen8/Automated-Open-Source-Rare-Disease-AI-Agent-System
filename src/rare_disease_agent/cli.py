"""Command-line interface for safe local development utilities."""

from __future__ import annotations

import json
import re
import uuid
from importlib.resources import as_file, files
from pathlib import Path
from typing import Annotated

import typer

from rare_disease_agent import __version__
from rare_disease_agent.agents.variant_agent import VariantFilteringAgent
from rare_disease_agent.config import load_settings
from rare_disease_agent.llm.registry import default_registry
from rare_disease_agent.models import (
    check_model_suitability,
    inspect_hardware,
    recommend_models,
)
from rare_disease_agent.models.hardware import GIB
from rare_disease_agent.storage.parquet import write_variants_parquet
from rare_disease_agent.tools.variants.vcf import parse_vcf
from rare_disease_agent.workflows.mock_strategy import default_mock_decisions
from rare_disease_agent.workflows.track1_filtering import VariantFilteringWorkflow

app = typer.Typer(
    name="rare-disease-agent",
    help="Local-first rare-disease research workflow (not for clinical use).",
    no_args_is_help=True,
)
models_app = typer.Typer(help="Inspect model/runtime options without downloading weights.")
variants_app = typer.Typer(help="Deterministic variant data preparation commands.")
app.add_typer(models_app, name="models")
app.add_typer(variants_app, name="variants")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the installed version.",
    ),
) -> None:
    """Rare Disease Agent command line."""


@models_app.command("inspect-hardware")
def inspect_hardware_command(
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Inspect memory, architecture, GPU assumptions, and disk space."""

    hardware = inspect_hardware()
    if as_json:
        typer.echo(json.dumps(hardware.model_dump(), indent=2))
        return
    typer.echo(f"OS:               {hardware.operating_system}")
    typer.echo(f"Architecture:     {hardware.architecture}")
    typer.echo(f"CPU:              {hardware.cpu}")
    typer.echo(f"Memory:           {hardware.total_memory_gb:.1f} GB total")
    typer.echo(f"Currently free:   {hardware.available_memory_gb:.1f} GB")
    if hardware.unified_memory_gb is not None:
        typer.echo(f"Unified memory:   {hardware.unified_memory_gb:.1f} GB")
    typer.echo(f"GPU:              {hardware.gpu}")
    typer.echo(f"Metal supported:  {'yes' if hardware.metal_supported else 'no/unknown'}")
    typer.echo("CUDA assumed:     no")
    typer.echo(f"Free disk:        {hardware.free_disk_gb:.1f} GB")


@models_app.command("recommend")
def recommend_command(
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Recommend realistic local models; never download or launch them."""

    recommendation = recommend_models(inspect_hardware())
    if as_json:
        typer.echo(json.dumps(recommendation.model_dump(), indent=2))
        return

    hardware = recommendation.hardware
    typer.echo(
        f"Detected {hardware.cpu} with {hardware.total_memory_gb:.1f} GB "
        f"{'unified ' if hardware.unified_memory_gb else ''}memory."
    )
    typer.echo(
        "Conservative local ceiling: "
        f"{recommendation.local_parameter_ceiling_billion:g}B parameters."
    )
    if recommendation.candidates:
        typer.echo("\nRecommended local candidates:")
        for candidate in recommendation.candidates:
            status = "ready" if candidate.launch_ready_now else "close apps before launch"
            typer.echo(
                f"- {candidate.model} ({candidate.backend}, {candidate.quantization}, "
                f"~{candidate.approximate_weight_gb:g} GB weights; {status})"
            )
    else:
        typer.echo("\nNo 7B-14B candidate fits this machine conservatively.")
    if recommendation.warnings:
        typer.echo("\nWarnings:")
        for warning in recommendation.warnings:
            typer.echo(f"- {warning}")
    typer.echo("\nDevelopment policy:")
    for rule in recommendation.policy:
        typer.echo(f"- {rule}")
    typer.echo("\nNo model was downloaded or started.")


@models_app.command("list")
def list_models() -> None:
    """Explain where configured model choices live."""

    typer.echo("Configured development profiles: configs/models.yaml")
    typer.echo("Run `rare-disease-agent models recommend` before selecting a local model.")
    typer.echo("This command does not query a registry or download weights.")


@models_app.command("check")
def check_model_command(
    model: Annotated[str, typer.Argument(help="Local model tag to evaluate.")],
    allow_oversized: Annotated[
        bool,
        typer.Option(
            "--allow-oversized",
            help="Bypass only the conservative host-size ceiling; never bypass live-memory checks.",
        ),
    ] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Evaluate local model safety without downloading or starting it."""

    suitability = check_model_suitability(
        model,
        inspect_hardware(),
        allow_oversized=allow_oversized,
    )
    if as_json:
        typer.echo(json.dumps(suitability.model_dump(), indent=2))
    else:
        status = "allowed" if suitability.allowed else "refused"
        typer.echo(f"{model}: {status}")
        for reason in suitability.reasons:
            typer.echo(f"- {reason}")
        typer.echo("No model was downloaded or started.")
    if not suitability.allowed:
        raise typer.Exit(code=2)


@variants_app.command("prepare")
def prepare_variants(
    input_path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    output_path: Annotated[Path, typer.Argument(dir_okay=False)],
    overwrite: Annotated[
        bool, typer.Option("--overwrite", help="Replace an existing output file.")
    ] = False,
) -> None:
    """Convert a local VCF into typed Parquet without invoking an LLM."""

    if output_path.exists() and not overwrite:
        raise typer.BadParameter(f"Output already exists: {output_path}; pass --overwrite")
    disk_probe = output_path.parent if output_path.parent.exists() else Path(".")
    resources = inspect_hardware(disk_probe)
    minimum_disk_gb = max(0.1, (input_path.stat().st_size * 2) / GIB)
    if resources.free_disk_gb < minimum_disk_gb:
        raise typer.BadParameter(
            f"Insufficient disk headroom: need ~{minimum_disk_gb:.1f} GB, "
            f"found {resources.free_disk_gb:.1f} GB"
        )
    if resources.available_memory_gb < 2:
        typer.echo(
            "Warning: less than 2 GB memory is currently available; conversion is streaming "
            "but other applications should be closed before a large run.",
            err=True,
        )
    count = write_variants_parquet(parse_vcf(input_path), output_path)
    typer.echo(f"Wrote {count} variant records to {output_path}")


@app.command("agent-filter")
def agent_filter(
    input_source: Annotated[
        str,
        typer.Option(
            "--input",
            help="Use 'synthetic' or an explicitly marked synthetic VCF-like fixture.",
        ),
    ],
    backend: Annotated[
        str, typer.Option("--backend", help="LLM backend: mock or ollama.")
    ] = "mock",
    model: Annotated[str | None, typer.Option("--model", help="Explicit Ollama model tag.")] = None,
    output_dir: Annotated[
        Path | None,
        typer.Option("--output-dir", file_okay=False, help="Exact run output directory."),
    ] = None,
    run_id: Annotated[str | None, typer.Option("--run-id")] = None,
    config_path: Annotated[
        Path, typer.Option("--config", exists=True, dir_okay=False, readable=True)
    ] = Path("configs/default.yaml"),
    causal_variant_id: Annotated[
        str | None,
        typer.Option("--causal-variant-id", help="Synthetic truth ID used only for evaluation."),
    ] = None,
    allow_oversized: Annotated[
        bool,
        typer.Option(
            "--allow-oversized",
            help="Allow 14B only if live-memory and the absolute 14B cap still pass.",
        ),
    ] = False,
) -> None:
    """Run autonomous, synthetic-only, agent-controlled deterministic filtering."""

    normalized_backend = backend.strip().lower()
    if normalized_backend not in {"mock", "ollama"}:
        raise typer.BadParameter("--backend must be 'mock' or 'ollama'")
    settings = load_settings(config_path)
    selected_run_id = run_id or f"phase2-{uuid.uuid4().hex[:12]}"
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", selected_run_id):
        raise typer.BadParameter(
            "--run-id must contain 1-64 letters, digits, underscores, or hyphens."
        )
    destination = output_dir or settings.paths.run_dir / selected_run_id
    if destination.exists() and any(destination.iterdir()):
        raise typer.BadParameter(f"Run output directory is not empty: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    parquet_path = destination / "synthetic_variants.parquet"

    if input_source == "synthetic":
        resource = files("rare_disease_agent.resources").joinpath("synthetic_phase2.vcf.txt")
        with as_file(resource) as fixture_path:
            write_variants_parquet(parse_vcf(fixture_path), parquet_path)
        known_causal = causal_variant_id or "SYNTH-CAUSAL-001"
    else:
        fixture_path = Path(input_source)
        if not fixture_path.is_file():
            raise typer.BadParameter(f"Synthetic input does not exist: {fixture_path}")
        try:
            with fixture_path.open(encoding="utf-8") as handle:
                prefix = handle.read(8_192)
        except UnicodeDecodeError as exc:
            raise typer.BadParameter("Synthetic input must be UTF-8 VCF text.") from exc
        if "##synthetic=true" not in prefix:
            raise typer.BadParameter(
                "Phase 2 accepts only explicitly marked synthetic inputs; "
                "real patient data is out of scope."
            )
        write_variants_parquet(parse_vcf(fixture_path), parquet_path)
        known_causal = causal_variant_id

    if normalized_backend == "mock":
        llm_backend = default_registry.create("mock", structured_responses=default_mock_decisions())
    else:
        selected_model = model or settings.models.orchestrator.model
        if not selected_model:
            raise typer.BadParameter("An Ollama model must be configured or supplied with --model.")
        suitability = check_model_suitability(
            selected_model,
            inspect_hardware(),
            allow_oversized=allow_oversized,
        )
        if not suitability.allowed:
            raise typer.BadParameter(
                "Ollama preflight refused execution: " + " ".join(suitability.reasons)
            )
        llm_backend = default_registry.create(
            "ollama",
            model=selected_model,
            base_url=settings.models.orchestrator.base_url or "http://127.0.0.1:11434",
            allow_oversized=allow_oversized,
        )

    workflow = VariantFilteringWorkflow(
        parquet_path=parquet_path,
        agent=VariantFilteringAgent(llm_backend),
        run_directory=destination,
        config=settings.agents.variant_filtering,
        run_id=selected_run_id,
        causal_variant_id=known_causal,
    )
    result = workflow.run()
    typer.echo(json.dumps(result.metrics.model_dump(), indent=2))
    typer.echo(f"Audit: {result.audit_path}")
    typer.echo(f"Candidates: {result.candidates_path}")
