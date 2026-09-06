"""Explicit resource CLI; no command fetches implicitly."""

from pathlib import Path
from typing import Annotated

import typer

from rare_disease_agent.resource_management import ResourceManager, read_locks
from rare_disease_agent.resource_management.hpo import normalize_hpo

app = typer.Typer(
    invoke_without_command=True,
    help="Plan, fetch explicitly, or verify pinned public resources offline.",
)


@app.callback()
def default(context: typer.Context):
    if context.invoked_subcommand is None:
        typer.echo("Offline mode. Use resources status LOCKFILE to inspect resources.")


@app.command()
def plan(lockfile: Path):
    for lock in read_locks(lockfile):
        typer.echo(lock.model_dump_json())


@app.command()
def status(lockfile: Path, cache: Annotated[Path, typer.Option()] = Path("cache/public")):
    manager = ResourceManager(cache)
    for lock in read_locks(lockfile):
        typer.echo(manager.status(lock))


@app.command()
def verify(lockfile: Path, cache: Annotated[Path, typer.Option()] = Path("cache/public")):
    manager = ResourceManager(cache)
    for lock in read_locks(lockfile):
        manager.verify(lock)
        typer.echo(f"Verified {lock.name}")


@app.command()
def fetch(
    lockfile: Path,
    name: str = typer.Option(...),
    cache: Annotated[Path, typer.Option()] = Path("cache/public"),
):
    locks = read_locks(lockfile)
    selected = next((lock for lock in locks if lock.name == name), None)
    if selected is None:
        raise typer.BadParameter("Unknown resource name")
    typer.echo(ResourceManager(cache).fetch(selected).model_dump_json())


@app.command("normalize-hpo")
def normalize(
    lockfile: Path, output: Path, cache: Annotated[Path, typer.Option()] = Path("cache/public")
):
    typer.echo(normalize_hpo(ResourceManager(cache), read_locks(lockfile), output))
