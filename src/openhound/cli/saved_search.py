from enum import Enum
from pathlib import Path
from typing import Annotated

import typer

from openhound.core.progress import Progress
from openhound.core.saved_searches import SavedSearches, Strategy

saved_searches = typer.Typer(help="Saved searches management")


class Format(str, Enum):
    json = "json"
    yaml = "yaml"


@saved_searches.command(help="Upload saved searches to BloodHound")
def upload(
    path: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=False,
            dir_okay=True,
            readable=True,
            resolve_path=True,
            help="Directory where saved searches are located",
        ),
    ],
    file_format: Format = typer.Option(
        default=Format.json,
        help="File format of the saved searches (json or yaml)",
    ),
    strategy: Strategy = typer.Option(
        default=Strategy.skip,
        help="Skip or overwrite saved search if the name already exists",
    ),
    progress: Progress = typer.Option(
        Progress.tqdm, help="Select progress tracker option"
    ),
):
    search_files = (
        path.rglob("**/*.json")
        if file_format == Format.json
        else path.rglob("**/*.yaml")
    )
    pipeline = SavedSearches(progress=progress, strategy=strategy)
    results = pipeline.run(list(search_files), file_format=file_format)
    return results
