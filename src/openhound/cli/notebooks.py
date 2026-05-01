import secrets
import string
from pathlib import Path
from typing import Annotated, Literal

import typer

BASE_PATH = Path(__file__).resolve().parents[1] / "notebooks"
TOKEN_LENGTH = 32

NOTEBOOKS = {
    "pipeline": BASE_PATH / "pipeline.py",
}

notebooks_app = typer.Typer(help="Start OpenHound Marimo notebooks")


def _generate_token(length: int = TOKEN_LENGTH) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


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
):
    """Start one of the bundled OpenHound Marimo notebooks."""
    from rich.console import Console

    console = Console()
    try:
        from marimo._server.file_router import AppFileRouter
        from marimo._server.start import start
        from marimo._server.tokens import AuthToken
        from marimo._session.model import SessionMode
        from marimo._utils.marimo_path import MarimoPath

    except ImportError:
        console.print(
            "[red]Error:[/red] Marimo is not installed. Install OpenHound with Marimo extras using openhound\\[notebook] [red]"
        )
        raise typer.Exit(1)

    notebook_path = NOTEBOOKS[notebook]
    start(
        file_router=AppFileRouter.from_filename(MarimoPath(str(notebook_path))),
        mode=SessionMode.RUN,
        development_mode=False,
        quiet=False,
        include_code=False,
        ttl_seconds=120,
        headless=False,
        port=port,
        host=host,
        proxy=None,
        watch=False,
        cli_args={},
        argv=[],
        base_url="",
        allow_origins=None,
        auth_token=AuthToken(_generate_token()),
        redirect_console_to_browser=False,
        skew_protection=True,
    )
