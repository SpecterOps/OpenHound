from pathlib import Path
from typing import Annotated, Literal

import typer

BASE_PATH = Path(__file__).resolve().parents[1] / "notebooks"

NOTEBOOKS = {
    "pipeline": BASE_PATH / "pipeline.py",
}

notebooks_app = typer.Typer(help="Start OpenHound Marimo notebooks")


@notebooks_app.command()
def start(
    notebook: Annotated[
        Literal["pipeline"], typer.Argument(help="Notebook to start")
    ] = "pipeline",
    host: Annotated[
        str,
        typer.Option("--host", "-h", help="Host for the Marimo server"),
    ] = "127.0.0.1",
    port: Annotated[
        int,
        typer.Option("--port", "-p", help="Port for the Marimo server"),
    ] = 2718,
    log_level: Annotated[
        Literal["critical", "error", "warning", "info"],
        typer.Option("--log-level", "-l", help="Uvicorn logging level"),
    ] = "info",
):
    """Start one of the bundled OpenHound Marimo notebooks."""
    from rich.console import Console

    console = Console()
    try:
        import uvicorn
        from fastapi import FastAPI
        from marimo import create_asgi_app

    except ImportError:
        console.print(
            "[red]Error:[/red] Marimo is not installed. Install OpenHound with Marimo extras using openhound\\[notebook] [red]"
        )
        raise typer.Exit(1)

    notebook_path = NOTEBOOKS[notebook]
    server = create_asgi_app().with_app(path="/", root=str(notebook_path))

    app = FastAPI()
    app.mount("/", server.build())

    console.print(
        "[bold green]Starting notebook server, press CTL+C twice to stop[/bold green]"
    )
    uvicorn.run(app, host=host, port=port, log_level=log_level)
