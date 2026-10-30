"""Find OpenGraph collection files and send them to BloodHound through a file-upload job.

A collection is the data a collector produced: OpenGraph JSON documents, each holding nodes
and edges plus a ``metadata`` section naming the source kind. OpenHound's ``convert`` stage
writes a folder of such files; other collectors, such as ConfigManBearPig, write a zip of
them. BloodHound accepts both through its file-upload job endpoints, which work the same way
in Community Edition and Enterprise:

1. start a job,
2. attach every file to it,
3. end the job, after which BloodHound reads the files and runs its analysis.

This module does the finding and the sending; the ``openhound graph upload`` command in
:mod:`openhound.cli.graph` is the thin layer on top.
"""

import gzip
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

# Imported by name so tests can replace only this module's clock and pause, without
# touching the time module that other libraries rely on.
from time import monotonic, sleep

import requests
from dlt.common import json

from openhound.core.clients.bloodhound import BloodHoundClient, BloodHoundHTTPError
from openhound.core.clients.models import FileUploadJob

logger = logging.getLogger(__name__)

# The content types BloodHound accepts for a collection file.
CONTENT_TYPE_JSON = "application/json"
CONTENT_TYPE_ZIP = "application/zip"

# The most nodes plus edges sent in one request by default. Collections can hold millions of
# entities; sending them in bounded parts keeps each request small enough to succeed, lets a
# failure cost one part instead of the whole file, and lets BloodHound start working earlier.
DEFAULT_BATCH_SIZE = 5000

# How often to ask BloodHound about a job while waiting for it to finish, in seconds.
DEFAULT_POLL_SECONDS = 2.0


class CollectionNotFound(Exception):
    """Raised when the path does not lead to any collection file."""


class InvalidCollectionFile(Exception):
    """Raised when a collection file cannot be read or is not an OpenGraph JSON document."""


@dataclass
class FileResult:
    """What happened to one collection file.

    Attributes:
        path: The file.
        outcome: "uploaded", "skipped" (dry run), or "failed".
        detail: Extra explanation, used for failures and for files sent in several parts.
    """

    path: Path
    outcome: str
    detail: str = ""

    @property
    def succeeded(self) -> bool:
        return self.outcome in ("uploaded", "skipped")


@dataclass
class GraphUploadResult:
    """The outcome of one ``graph upload`` run.

    Attributes:
        job_id: The BloodHound job the files were attached to, or None for a dry run or when
            the job could not be started.
        files: One result per file, in upload order.
        job: The job's final state when the caller waited for it; otherwise None.
        detail: Explanation when the job itself could not be started or ended.
        job_ended: True once BloodHound accepted the request to end the job. BloodHound only
            starts processing a job after it ends, so there is nothing to wait for otherwise.
    """

    job_id: int | None
    files: list[FileResult] = field(default_factory=list)
    job: FileUploadJob | None = None
    detail: str = ""
    job_ended: bool = False

    @property
    def all_files_succeeded(self) -> bool:
        return all(result.succeeded for result in self.files)


def find_collection_files(target: str) -> list[Path]:
    """Work out which files to upload from what the person typed.

    Args:
        target (str): A collection file (``.json`` or ``.zip``) or a folder holding them.

    Returns:
        list[Path]: The files to upload, in name order.
    """
    candidate = Path(target)

    if candidate.is_file():
        return [candidate]

    if candidate.is_dir():
        files = sorted(
            path
            for path in candidate.iterdir()
            if path.is_file() and path.suffix.lower() in (".json", ".zip")
        )
        if not files:
            raise CollectionNotFound(
                f"The folder '{candidate}' contains no .json or .zip collection files."
            )
        return files

    raise CollectionNotFound(
        f"'{target}' is not a file and not a folder. Point the command at an OpenHound "
        "convert output folder, a single OpenGraph JSON file, or a zip of them."
    )


def _load_collection_file(path: Path) -> tuple[bytes, str, dict | None]:
    """Read a collection file; for JSON also return the parsed document, otherwise None."""
    try:
        contents = path.read_bytes()
    except OSError as error:
        raise InvalidCollectionFile(f"Could not read '{path}': {error}") from error

    if path.suffix.lower() == ".zip":
        return contents, CONTENT_TYPE_ZIP, None

    try:
        document = json.loads(contents.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as error:
        raise InvalidCollectionFile(f"'{path}' is not valid JSON: {error}") from error

    if not isinstance(document, dict) or "graph" not in document:
        raise InvalidCollectionFile(
            f"'{path}' is not an OpenGraph document: it has no top-level 'graph' section."
        )

    return contents, CONTENT_TYPE_JSON, document


def read_collection_file(path: Path) -> tuple[bytes, str]:
    """Read a collection file and decide which content type to send it as.

    JSON files are parsed once locally and checked for the ``graph`` section every OpenGraph
    document has, so a wrong file produces a clear local error naming it instead of a job
    that fails later inside BloodHound.

    Returns:
        tuple[bytes, str]: The raw bytes and the content type for the upload.
    """
    contents, content_type, _ = _load_collection_file(path)
    return contents, content_type


def split_document(document: dict, batch_size: int) -> Iterator[dict]:
    """Cut one OpenGraph document into documents of at most ``batch_size`` nodes plus edges.

    Every part keeps the original ``metadata`` section. All node parts come before any edge
    part, so the nodes an edge points at have been sent before the edge itself.

    Args:
        document (dict): A parsed OpenGraph document.
        batch_size (int): The most nodes plus edges allowed in one part.

    Yields:
        dict: Each part, a complete OpenGraph document.
    """
    graph = document["graph"]
    metadata = document.get("metadata")
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []

    def part(kind: str, items: list) -> dict:
        piece: dict = {"graph": {kind: items}}
        if metadata is not None:
            piece = {"metadata": metadata, **piece}
        return piece

    for start in range(0, len(nodes), batch_size):
        yield part("nodes", nodes[start : start + batch_size])
    for start in range(0, len(edges), batch_size):
        yield part("edges", edges[start : start + batch_size])


def _send_part(
    client: BloodHoundClient,
    job_id: int,
    body: bytes,
    content_type: str,
    file_name: str,
    compress: bool,
) -> None:
    """Attach one request body to the job, gzip-compressing JSON when asked."""
    content_encoding = None
    if compress and content_type == CONTENT_TYPE_JSON:
        original_size = len(body)
        body = gzip.compress(body)
        content_encoding = "gzip"
        logger.info(
            f"Compressed {file_name} from {original_size:,} to {len(body):,} bytes"
        )
    client.upload_file_to_job(
        job_id=job_id,
        body=body,
        content_type=content_type,
        file_name=file_name,
        content_encoding=content_encoding,
    )


def _upload_one_file(
    client: BloodHoundClient,
    job_id: int,
    path: Path,
    compress: bool,
    batch_size: int,
) -> FileResult:
    """Send one file, cutting a large JSON document into parts of at most ``batch_size``."""
    try:
        body, content_type, document = _load_collection_file(path)
    except InvalidCollectionFile as error:
        return FileResult(path=path, outcome="failed", detail=str(error))

    graph = document["graph"] if document is not None else {}
    entity_count = len(graph.get("nodes") or []) + len(graph.get("edges") or [])

    if document is None or batch_size <= 0 or entity_count <= batch_size:
        parts = [(path.name, body)]
    else:
        parts = [
            (f"{path.stem}.part-{number:04d}{path.suffix}", json.dumps(piece).encode())
            for number, piece in enumerate(split_document(document, batch_size), 1)
        ]
        logger.info(
            f"Split {path.name} ({entity_count:,} items) into {len(parts)} parts"
        )
    # The parsed document and the original bytes are no longer needed once parts exist.
    del document, body

    failures = []
    for file_name, part_body in parts:
        try:
            _send_part(client, job_id, part_body, content_type, file_name, compress)
        except BloodHoundHTTPError as error:
            failures.append(f"{file_name}: {error.reason}")
        except requests.RequestException as error:
            failures.append(f"{file_name}: {error}")

    if failures:
        return FileResult(path=path, outcome="failed", detail="; ".join(failures))
    detail = f"sent in {len(parts)} parts" if len(parts) > 1 else ""
    return FileResult(path=path, outcome="uploaded", detail=detail)


def upload_collection_files(
    client: BloodHoundClient,
    files: list[Path],
    dry_run: bool = False,
    compress: bool = True,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> GraphUploadResult:
    """Attach every file to one new job and end the job.

    Files that fail the local check are reported and skipped. Files that BloodHound rejects
    are reported, and the remaining files are still attached. The job is always ended once
    it was started, so BloodHound processes whatever did arrive.

    Args:
        client (BloodHoundClient): A signed-in BloodHound client.
        files (list[Path]): The files to upload.
        dry_run (bool): When True, read and check every file but contact BloodHound for nothing.
        compress (bool): When True, gzip each JSON request before sending it; collection files
            repeat the same keys millions of times, so they shrink a great deal. Zip files
            are already compressed and are sent as they are.
        batch_size (int): The most nodes plus edges in one request. A larger JSON file is
            cut into parts of this size, all attached to the same job. Zip files cannot be
            cut and are always sent whole. Zero or less turns batching off.

    Returns:
        GraphUploadResult: The job id and one result per file.
    """
    result = GraphUploadResult(job_id=None)

    # Check every file before touching BloodHound, so a typo never leaves a half-filled job.
    # Each file is read and then released, so only one file is in memory at a time.
    checked: list[Path] = []
    for path in files:
        try:
            read_collection_file(path)
        except InvalidCollectionFile as error:
            result.files.append(
                FileResult(path=path, outcome="failed", detail=str(error))
            )
            continue
        checked.append(path)

    if dry_run:
        for path in checked:
            result.files.append(
                FileResult(path=path, outcome="skipped", detail="dry run")
            )
        return result

    if not checked:
        result.detail = "No file passed the local check, so no job was started."
        return result

    try:
        job = client.start_file_upload_job()
    except (BloodHoundHTTPError, requests.RequestException) as error:
        reason = error.reason if isinstance(error, BloodHoundHTTPError) else str(error)
        result.detail = f"BloodHound refused to start a file-upload job: {reason}"
        for path in checked:
            result.files.append(
                FileResult(path=path, outcome="failed", detail="job not started")
            )
        return result

    result.job_id = job.id
    logger.info(f"Started BloodHound file-upload job {job.id}")

    for path in checked:
        result.files.append(
            _upload_one_file(client, job.id, path, compress, batch_size)
        )

    try:
        client.end_file_upload_job(job.id)
        result.job_ended = True
    except (BloodHoundHTTPError, requests.RequestException) as error:
        reason = error.reason if isinstance(error, BloodHoundHTTPError) else str(error)
        result.detail = f"Files were attached but the job could not be ended: {reason}"

    return result


def wait_for_job(
    client: BloodHoundClient,
    job_id: int,
    timeout_seconds: float,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
) -> FileUploadJob | None:
    """Ask BloodHound about the job until it finishes or the time runs out.

    Args:
        client (BloodHoundClient): A signed-in BloodHound client.
        job_id (int): The job to watch.
        timeout_seconds (float): Give up after this long.
        poll_seconds (float): How long to pause between questions.

    Returns:
        FileUploadJob | None: The job's last known state, or None if BloodHound never listed it.
    """
    deadline = monotonic() + timeout_seconds
    job = client.file_upload_job(job_id)

    while job is not None:
        status = job.status_code
        if status is not None and status.is_finished:
            return job
        if monotonic() >= deadline:
            logger.warning(
                f"Job {job_id} is still '{job.status_text}' after {timeout_seconds:.0f} seconds"
            )
            return job
        sleep(poll_seconds)
        job = client.file_upload_job(job_id)

    return None
