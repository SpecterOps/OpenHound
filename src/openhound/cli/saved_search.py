from enum import Enum
from pathlib import Path
from typing import Annotated

import typer
from rich import Console

from openhound.core.models.saved_search import QueryBundle
from openhound.core.progress import Progress
from openhound.core.saved_searches import SavedSearches, Strategy

saved_searches = typer.Typer(help="Saved searches management")


class Format(str, Enum):
    json = "json"
    yaml = "yaml"


class OutputFormat(str, Enum):
    json = "json"
    zip = "zip"


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


@saved_searches.command(help="Create a single saved searches json/zip bundle")
def bundle(
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
        output_path: Annotated[typer.FileTextWrite, typer.Argument(
            help="Output file path for the generated saved-search bundle (including filename)")],
        file_format: Format = typer.Option(
            default=Format.json,
            help="File format of the saved searches (json or yaml)",
        ),
        output_format: OutputFormat = typer.Option(
            default=OutputFormat.json,
            help="File format for the saved searches bundle (json or zip)",
        )
):
    search_files = list(
        path.rglob("**/*.json")
        if file_format == Format.json
        else path.rglob("**/*.yaml")
    )

    bundle_object = QueryBundle.from_paths(search_files, file_format=file_format)
    bundle_object.save(output_path, output_format=output_format)

    console = Console()
    console.print("[bold green]Saved-search bundle created[/bold green]")
    console.print(f"[bold magenta]Saved searches:[/bold magenta] {len(bundle_object.queries)}")
    console.print(
        f"[bold magenta]Output path:[/bold magenta] [italic]{Path(output_path.name).resolve()}[/italic]"
    )
