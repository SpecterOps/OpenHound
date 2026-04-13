from enum import Enum
from pathlib import Path
from typing import Annotated

import typer

from openhound.core.privilege_zones import PrivilegeZones, Strategy
from openhound.core.progress import Progress

privilege_zone = typer.Typer(help="Privilege zone selectors management")


class Format(str, Enum):
    json = "json"
    yaml = "yaml"


@privilege_zone.command(help="Upload privilege zone selectors to BloodHound")
def upload(
    path: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=False,
            dir_okay=True,
            readable=True,
            resolve_path=True,
            help="Directory where PZ config files are located",
        ),
    ],
    strategy: Strategy = typer.Option(
        default=Strategy.skip,
        help="Skip or overwrite privilege zone selector if the name already exists",
    ),
    file_format: Format = typer.Option(
        default=Format.json,
        help="File format of the privilege zone selectors (json or yaml)",
    ),
    progress: Progress = typer.Option(
        Progress.tqdm, help="Select progress tracker option"
    ),
):
    pz_files = (
        path.rglob("**/*.json")
        if file_format == Format.json
        else path.rglob("**/*.yaml")
    )
    pipeline = PrivilegeZones(progress=progress, strategy=strategy)
    results = pipeline.run(list(pz_files), file_format=file_format)
    return results
