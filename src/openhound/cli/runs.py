"""Inspect saved runs and recover local graph files without contacting BHE."""

import json
from pathlib import Path
from typing import Annotated

import typer
from filelock import Timeout

from openhound.core.manager import CollectorManager
from openhound.core.runs import SavedRun
from openhound.scheduler.dataflow import resume_saved_run

runs = typer.Typer(help="Inspect saved runs and recover graph files locally")
RunPath = Annotated[
    Path, typer.Argument(exists=True, file_okay=False, resolve_path=True)
]


@runs.command("inspect")
def inspect_run(directory: RunPath) -> None:
    """Show phase checkpoints and pending package IDs without attaching DLT."""
    try:
        typer.echo(json.dumps(SavedRun(directory).inspect(), indent=2))
    except Timeout:
        raise typer.BadParameter("Saved run is in use by another process") from None
    except (ValueError, OSError) as error:
        raise typer.BadParameter(str(error)) from None


@runs.command("resume")
def resume_run(
    directory: RunPath,
    workers: Annotated[int, typer.Option(min=1, help="Normalization workers")] = 1,
) -> None:
    """Recover graph files from saved resources; never start collection or upload."""
    try:
        run = SavedRun(directory)
        extension = next(
            (
                app
                for app in CollectorManager.from_entrypoint().collectors
                if app.name == run.manifest.collector
            ),
            None,
        )
        if extension is None:
            raise ValueError(
                f"Install the saved run's collector: {run.manifest.collector}"
            )
        output = resume_saved_run(extension, run, normalize_workers=workers)
        typer.echo(f"Recovered graph files: {output}\nNo data was uploaded to BHE.")
    except Timeout:
        raise typer.BadParameter("Saved run is in use by another process") from None
    except (ValueError, OSError) as error:
        raise typer.BadParameter(str(error)) from None
