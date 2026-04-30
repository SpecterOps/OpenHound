import re
from enum import Enum
from importlib.metadata import entry_points
from pathlib import Path
from typing import Optional, Annotated

import typer


class Template(str, Enum):
    mintlify = "mintlify"
    mkdocs = "mkdocs"


create_app = typer.Typer(
    help="Create a new OpenHound collector based on the cookiecutter template"
)


def generate_docs(base_path: Path, template: Template, group: str = "openhound.sources") -> None:
    """Loads the collector extensions via entrypoints and uses griffe to generate OpenHound collector docs.

    Args:
        base_path (Path): Base path where to create the collector docs (default: ./docs).
        group (str, optional): The entrypoint that collectors use  (default: "openhound.sources").

    """
    import griffe

    from openhound.docs.pipeline import CustomCollectorDocs, GraphResourceDecorator

    discover_extension = entry_points(group=group)
    griffe_ext = GraphResourceDecorator()
    griffe_extensions = griffe.load_extensions(griffe_ext)

    # For each discovered extension add the extension to griffe and extract the collector docs.
    for ext in discover_extension:
        ext_name = ext.dist.name.replace("-", "_")
        griffe.load(
            ext_name,
            extensions=griffe_extensions,
            resolve_aliases=True,
            resolve_external=True,
        )

    # For each loaded/extracted collector generate the markdown files
    for name, collector in griffe_ext.collectors.items():
        output_path = (
            base_path / "sources" / name if len(discover_extension) > 1 else base_path
        )
        docs = CustomCollectorDocs(
            name,
            base_docs_dir=base_path,
            assets=collector.assets,
            sources=collector.sources,
            resources=collector.resources,
            transformers=collector.transformers,
        )
        docs.to_markdown(output_path=output_path)


@create_app.command()
def docs(
        output_dir: Path = typer.Argument(
            Path("./docs"),
            help="Where to create the docs for extensions",
            file_okay=False,
            dir_okay=True,
            exists=True,
        ),
        template: Annotated[Template, typer.Option(
            help="Which template to use for docs generation")
        ] = Template.mkdocs,

):
    """Generate OpenHound collector docs based on @app.asset decorators and docstrings in each collector.

    Args:
        output_dir (Path): Base path where to create the collector docs (default: ./docs).
        template (Template): Template to use for docs generation

    """
    generate_docs(output_dir, template)


@create_app.command()
def collector(
        output_dir: Path = typer.Argument(
            help="Where to create the collector",
            file_okay=False,
            dir_okay=True,
        ),
        config: Optional[Path] = typer.Option(
            None,
            "--config",
            "-c",
            help="Path to config file (YAML/JSON)",
            exists=True,
            file_okay=True,
            dir_okay=False,
        ),
        template: str = typer.Option(
            "gh:SpecterOps/OpenHound-template", "--template", "-t", help="Template to use"
        ),
):
    """Generate a new OpenHound collector using a cookiecutter template.

    Args:
        output_dir (Path): Base path where to create the collector.
        config (Path, optional): Path to a pre-defined cookiecutter config file (YAML/JSON).
        template (str, optional): Template to use. Defaults to "gh:SpecterOps/OpenHound-template".

    """

    from cookiecutter.main import cookiecutter
    from rich.console import Console
    from rich.prompt import Confirm, Prompt

    console = Console()
    console.print("[bold green]OpenHound Collector Generator[/bold green]")
    console.print(
        "[yellow]This process will create a new collector using Cookiecutter and prompt you for required details.[/yellow]"
    )
    console.print(
        f"[bold magenta]Template path:[/bold magenta] [italic]{template}[/italic]"
    )
    console.print(
        f"[bold magenta]Output dir:[/bold magenta] [italic]{output_dir}[/italic]"
    )

    # Ask for the necessary details
    project_name = Prompt.ask("[bold cyan]Project name[/bold cyan]", console=console)
    target_service = Prompt.ask(
        "[bold cyan]Target service[/bold cyan]", console=console
    )
    author = Prompt.ask("[bold cyan]Author name[/bold cyan]", console=console)

    # Check if a valid email is used
    valid_author_email = False
    while not valid_author_email:
        author_email = Prompt.ask(
            "[bold cyan]Author email[/bold cyan]", console=console
        )
        if re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", author_email):
            valid_author_email = True
        else:
            console.print(
                f"[red]Error:[/red] Invalid author email [red]: {author_email}"
            )

    # Cookiecutter context which will be injected in the cookiecutter.json
    extra_context = {
        "project_name": project_name,
        "target_service": target_service,
        "author": author,
        "author_email": author_email,
    }

    full_path = output_dir / project_name
    if Confirm.ask(f"Generate collector at {full_path}?", console=console):
        console.print("[green]Generating collector...[/green]")
        cookiecutter(
            str(template),
            output_dir=str(output_dir),
            no_input=True,
            extra_context=extra_context,
            config_file=str(config) if config else None,
        )
        console.print("[bold green]Collector created successfully![/bold green]")
