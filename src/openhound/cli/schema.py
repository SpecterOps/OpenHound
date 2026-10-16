"""The ``openhound schema`` command group: install extension schemas into BloodHound."""

import logging

import typer

from openhound.core.clients.from_secrets import (
    MissingBloodHoundCredentials,
    bloodhound_client_from_secrets,
)
from openhound.core.schema_upload import (
    SchemaNotFound,
    find_schema_files,
    upload_schema_files,
)

logger = logging.getLogger(__name__)

schema = typer.Typer(help="OpenGraph extension schema management")


@schema.callback()
def schema_group():
    """Manage OpenGraph extension schemas in BloodHound.

    This callback exists so that ``schema`` behaves as a command group with named
    subcommands, such as ``openhound schema upload``, even while it has only one. Without
    it, Typer would collapse the group and the subcommand name would be lost.
    """


@schema.command(help="Install or update extension schemas in BloodHound")
def upload(
    target: str = typer.Argument(
        ...,
        help=(
            "A schema file (.json or .zip bundle), a folder of schema files, "
            "or the name of an installed collector that ships a schema.json"
        ),
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Check the files and show what would be uploaded without contacting BloodHound",
    ),
):
    """Find the schema files, upload each one, and print a line per file.

    The command exits with status 1 when any file failed, so scripts can rely on it.
    """
    try:
        files = find_schema_files(target)
    except SchemaNotFound as error:
        typer.secho(str(error), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    if dry_run:
        # A dry run never needs credentials, so a missing configuration must not stop it.
        client = None
    else:
        try:
            client = bloodhound_client_from_secrets()
        except MissingBloodHoundCredentials as error:
            typer.secho(str(error), fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)

    results = upload_schema_files(client=client, files=files, dry_run=dry_run)  # type: ignore[arg-type]

    any_failed = False
    for result in results:
        if result.succeeded:
            typer.secho(f"{result.outcome:>8}  {result.path}", fg=typer.colors.GREEN)
        else:
            any_failed = True
            typer.secho(
                f"{result.outcome:>8}  {result.path}: {result.detail}",
                fg=typer.colors.RED,
                err=True,
            )

    if any_failed:
        raise typer.Exit(code=1)
