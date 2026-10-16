"""The ``openhound graph`` command group: send collected OpenGraph data to BloodHound."""

import logging

import requests
import typer

from openhound.core.clients.bloodhound import BloodHoundHTTPError
from openhound.core.clients.from_secrets import (
    MissingBloodHoundCredentials,
    bloodhound_client_from_secrets,
)
from openhound.core.clients.models import FileUploadJobStatus
from openhound.core.graph_upload import (
    DEFAULT_BATCH_SIZE,
    CollectionNotFound,
    find_collection_files,
    upload_collection_files,
    wait_for_job,
)

logger = logging.getLogger(__name__)

graph = typer.Typer(help="OpenGraph collection upload")


@graph.callback()
def graph_group():
    """Send collected OpenGraph data to BloodHound.

    This callback keeps ``graph`` a command group with named subcommands, such as
    ``openhound graph upload``, even while it has only one.
    """


@graph.command(help="Upload collection files to BloodHound through a file-upload job")
def upload(
    target: str = typer.Argument(
        ...,
        help=(
            "An OpenHound convert output folder, a single OpenGraph JSON file, "
            "or a zip of OpenGraph JSON files"
        ),
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Check the files and show what would be uploaded without contacting BloodHound",
    ),
    compress: bool = typer.Option(
        True,
        "--compress/--no-compress",
        help="Gzip JSON files before sending them (zip files are never recompressed)",
    ),
    batch_size: int = typer.Option(
        DEFAULT_BATCH_SIZE,
        "--batch-size",
        help=(
            "Most nodes plus edges per request; larger JSON files are cut into parts of "
            "this size within the same job. Use 0 to send each file whole"
        ),
    ),
    wait: bool = typer.Option(
        True,
        "--wait/--no-wait",
        help="After uploading, wait until BloodHound has finished processing the job",
    ),
    timeout: float = typer.Option(
        600.0,
        "--timeout",
        help="How many seconds to wait for the job before giving up",
    ),
):
    """Find the files, attach them to one job, end the job, then optionally wait for it.

    The command exits with status 1 when any file failed, when the job could not be started
    or ended, or when BloodHound reports the job failed.
    """
    try:
        files = find_collection_files(target)
    except CollectionNotFound as error:
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

    result = upload_collection_files(
        client=client,  # type: ignore[arg-type]
        files=files,
        dry_run=dry_run,
        compress=compress,
        batch_size=batch_size,
    )

    for file_result in result.files:
        if file_result.succeeded:
            note = f" ({file_result.detail})" if file_result.detail else ""
            typer.secho(
                f"{file_result.outcome:>8}  {file_result.path}{note}",
                fg=typer.colors.GREEN,
            )
        else:
            typer.secho(
                f"{file_result.outcome:>8}  {file_result.path}: {file_result.detail}",
                fg=typer.colors.RED,
                err=True,
            )

    failed = not result.all_files_succeeded
    if result.detail:
        failed = True
        typer.secho(result.detail, fg=typer.colors.RED, err=True)

    if result.job_id is not None:
        typer.echo(f"BloodHound file-upload job: {result.job_id}")

        # Wait only for a job that was ended: BloodHound never processes one left open, so
        # waiting would only use up the whole --timeout before reporting the failure.
        if wait and client is not None and result.job_ended:
            try:
                job = wait_for_job(client, result.job_id, timeout_seconds=timeout)
            except (BloodHoundHTTPError, requests.RequestException) as error:
                failed = True
                reason = (
                    error.reason
                    if isinstance(error, BloodHoundHTTPError)
                    else str(error)
                )
                typer.secho(
                    f"Lost contact with BloodHound while waiting for job "
                    f"{result.job_id}: {reason}. Check Administration > File Ingest.",
                    fg=typer.colors.RED,
                    err=True,
                )
                raise typer.Exit(code=1)
            if job is None:
                failed = True
                typer.secho(
                    f"BloodHound no longer lists job {result.job_id}.",
                    fg=typer.colors.RED,
                    err=True,
                )
            else:
                summary = f"Job {job.id} is {job.status_text}"
                if job.total_files:
                    summary += (
                        f" ({job.failed_files} of {job.total_files} files failed)"
                    )
                if job.status_message:
                    summary += f": {job.status_message}"
                job_failed = job.status_code in (
                    FileUploadJobStatus.FAILED,
                    FileUploadJobStatus.CANCELED,
                    FileUploadJobStatus.TIMED_OUT,
                    FileUploadJobStatus.PARTIALLY_COMPLETE,
                )
                still_running = (
                    job.status_code is None or not job.status_code.is_finished
                )
                if job_failed or still_running:
                    failed = True
                    typer.secho(summary, fg=typer.colors.RED, err=True)
                else:
                    typer.secho(summary, fg=typer.colors.GREEN)
        elif result.job_ended:
            typer.echo(
                "Watch its progress under Administration > File Ingest in BloodHound."
            )

    if failed:
        raise typer.Exit(code=1)
