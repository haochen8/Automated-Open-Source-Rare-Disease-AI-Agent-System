"""Command-line interface for safe local development utilities."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from rare_disease_agent import __version__
from rare_disease_agent.models import inspect_hardware, recommend_models
from rare_disease_agent.models.hardware import GIB
from rare_disease_agent.storage.parquet import write_variants_parquet
from rare_disease_agent.tools.variants.vcf import parse_vcf

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
