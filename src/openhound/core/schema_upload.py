"""Find OpenGraph extension schema files and send them to BloodHound.

An extension schema tells BloodHound which node kinds and relationship kinds a collector
produces, with their display names, icons, and colors. BloodHound must know the schema before
it can show collected data properly. This module does the finding and the sending; the
``openhound schema upload`` command in :mod:`openhound.cli.schema` is the thin layer on top.

The thing to upload can be named three ways:

* A single file: ``schema.json`` or a ``bundle.zip`` that contains a ``schema.json``.
* A folder: every ``.json`` and ``.zip`` file directly inside it is uploaded.
* The name of an installed collector, such as ``sccm``: the ``schema.json`` that the
  collector ships next to its ``extension.yaml`` is uploaded.
"""

import logging
from dataclasses import dataclass
from importlib import resources
from importlib.metadata import entry_points
from pathlib import Path

import requests
from dlt.common import json

from openhound.core.clients.bloodhound import BloodHoundClient, BloodHoundHTTPError

logger = logging.getLogger(__name__)

# The entry point group that every OpenHound collector registers itself under. The same
# value is used by the collector manager when it discovers installed collectors.
COLLECTOR_ENTRY_POINT_GROUP = "openhound.sources"

# The file name a collector uses for the schema it ships with its code.
PACKAGED_SCHEMA_FILE_NAME = "schema.json"

# The content types BloodHound accepts for an extension upload.
CONTENT_TYPE_JSON = "application/json"
CONTENT_TYPE_ZIP = "application/zip"


class SchemaNotFound(Exception):
    """Raised when the path or collector name does not lead to any schema file."""


class InvalidSchemaFile(Exception):
    """Raised when a schema file cannot be read or is not valid JSON."""


@dataclass
class SchemaUploadResult:
    """What happened to one schema file.

    Attributes:
        path: The file that was uploaded.
        outcome: "created", "updated", "skipped" (dry run), or "failed".
        detail: Extra explanation, used for failures.
    """

    path: Path
    outcome: str
    detail: str = ""

    @property
    def succeeded(self) -> bool:
        return self.outcome in ("created", "updated", "skipped")


def find_schema_files(target: str) -> list[Path]:
    """Work out which files to upload from what the person typed.

    Args:
        target (str): A file path, a folder path, or the name of an installed collector.

    Returns:
        list[Path]: The schema files to upload, in a stable order.
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
            raise SchemaNotFound(
                f"The folder '{candidate}' contains no .json or .zip schema files."
            )
        return files

    packaged_schema = find_packaged_schema(target)
    if packaged_schema is not None:
        return [packaged_schema]

    raise SchemaNotFound(
        f"'{target}' is not a file, not a folder, and not the name of an installed "
        f"collector that ships a '{PACKAGED_SCHEMA_FILE_NAME}'. Installed collectors "
        f"are: {', '.join(installed_collector_names()) or 'none'}."
    )


def installed_collector_names() -> list[str]:
    """List the names of every collector installed in this Python environment."""
    return sorted(
        entry_point.name
        for entry_point in entry_points(group=COLLECTOR_ENTRY_POINT_GROUP)
    )


def find_packaged_schema(collector_name: str) -> Path | None:
    """Locate the schema file that an installed collector ships with its code.

    A collector registers itself with a line like ``sccm = "openhound_sccm.main:app"``.
    The part before the first dot, ``openhound_sccm`` here, is the Python package that holds
    the collector's files. The schema is expected at ``schema.json`` inside that package,
    beside the ``extension.yaml`` that OpenHound already requires there.

    Args:
        collector_name (str): The registered collector name, for example ``sccm``.

    Returns:
        Path | None: The schema file, or None when the collector or its schema is not installed.
    """
    for entry_point in entry_points(group=COLLECTOR_ENTRY_POINT_GROUP):
        if entry_point.name != collector_name:
            continue

        package_name = entry_point.module.split(".")[0]
        schema_location = resources.files(package_name) / PACKAGED_SCHEMA_FILE_NAME
        if schema_location.is_file():
            # resources.files() can describe files inside a zipped package; converting
            # through str() gives us a normal filesystem path for the ordinary case.
            return Path(str(schema_location))

        logger.warning(
            f"Collector '{collector_name}' is installed but ships no "
            f"'{PACKAGED_SCHEMA_FILE_NAME}' in its '{package_name}' package."
        )
        return None

    return None


def read_schema_file(path: Path) -> tuple[bytes, str]:
    """Read a schema file and decide which content type to send it as.

    JSON files are parsed once before sending so that a typo in the file produces a clear
    local error instead of a confusing answer from the server.

    Returns:
        tuple[bytes, str]: The raw bytes and the content type for the upload.
    """
    try:
        contents = path.read_bytes()
    except OSError as error:
        raise InvalidSchemaFile(f"Could not read '{path}': {error}") from error

    if path.suffix.lower() == ".zip":
        return contents, CONTENT_TYPE_ZIP

    try:
        json.loads(contents.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as error:
        raise InvalidSchemaFile(f"'{path}' is not valid JSON: {error}") from error

    return contents, CONTENT_TYPE_JSON


def upload_schema_files(
    client: BloodHoundClient,
    files: list[Path],
    dry_run: bool = False,
) -> list[SchemaUploadResult]:
    """Upload each schema file in turn and report what happened to each one.

    One bad file does not stop the others: every file gets its own result, and the caller
    decides what to do when some failed.

    Args:
        client (BloodHoundClient): A signed-in BloodHound client.
        files (list[Path]): The files to upload.
        dry_run (bool): When True, read and check every file but send nothing.

    Returns:
        list[SchemaUploadResult]: One result per file, in the same order as ``files``.
    """
    results: list[SchemaUploadResult] = []

    for path in files:
        try:
            body, content_type = read_schema_file(path)
        except InvalidSchemaFile as error:
            results.append(
                SchemaUploadResult(path=path, outcome="failed", detail=str(error))
            )
            continue

        if dry_run:
            results.append(
                SchemaUploadResult(path=path, outcome="skipped", detail="dry run")
            )
            continue

        try:
            updated = client.upsert_extension(body=body, content_type=content_type)
        except BloodHoundHTTPError as error:
            results.append(
                SchemaUploadResult(path=path, outcome="failed", detail=error.reason)
            )
            continue
        except requests.RequestException as error:
            # Connection refused, DNS failure, or a timeout: record it and carry on.
            results.append(
                SchemaUploadResult(path=path, outcome="failed", detail=str(error))
            )
            continue

        results.append(
            SchemaUploadResult(path=path, outcome="updated" if updated else "created")
        )

    return results
