"""Send ``openhound convert --method ingest`` output to BloodHound Community Edition.

BloodHound Enterprise receives converted data through its ``/api/v2/ingest`` endpoint, which
OpenHound's Enterprise destination already uses. Community Edition does not have that endpoint.
It accepts data only through file-upload jobs, the same mechanism its File Ingest page uses:

1. start a job,
2. attach files to it,
3. end the job, after which BloodHound reads the files and runs its analysis.

This module decides which of the two routes a ``convert --method ingest`` run takes, and provides
the destination that attaches each batch of converted data to the job. The job itself is started
and ended by :class:`openhound.core.convert.Converter`, because a dlt destination is called once
per batch and has no "before the first batch" or "after the last batch" step.

Only the ``convert`` command line uses this module. The Enterprise scheduler service calls the
converter directly and always takes the unchanged Enterprise route.
"""

import gzip
import itertools
import logging
import threading

import dlt
import requests
from dlt.common import json
from dlt.common.destination import Destination
from dlt.common.schema import TTableSchema
from dlt.common.typing import TDataItems

from openhound.core.clients.bloodhound import BloodHoundClient, BloodHoundHTTPError
from openhound.core.clients.from_secrets import (
    MissingBloodHoundCredentials,
    bloodhound_client_from_secrets,
)
from openhound.core.graph_upload import CONTENT_TYPE_JSON

logger = logging.getLogger(__name__)

# The value of ``product_edition`` that BloodHound Community Edition reports from /api/version.
EDITION_COMMUNITY = "community"

# How many converted items go into one attached file. This matches the Enterprise destination,
# so both editions receive the same amount of data per request. An item is one node or a list
# of up to 150 relationships, as produced by the OpenGraph source.
ITEMS_PER_FILE = 1000


def community_edition_client_for_ingest() -> BloodHoundClient | None:
    """Return a client when the configured BloodHound is Community Edition, otherwise None.

    The credentials come from ``[destination.bloodhound]``, falling back to
    ``[destination.bloodhoundenterprise]`` (see :mod:`openhound.core.clients.from_secrets`).
    BloodHound is then asked which edition it is.

    None means "use the Enterprise route exactly as before". That happens when no credentials
    are configured, when the server says it is Enterprise, and when the question cannot be
    answered at all. Falling back in the last case keeps every setup that worked before this
    change working the same way.
    """
    try:
        client = bloodhound_client_from_secrets()
    except MissingBloodHoundCredentials:
        logger.debug("No BloodHound settings found; using the Enterprise ingest route")
        return None

    try:
        edition = client.product_edition()
    except (
        BloodHoundHTTPError,
        requests.RequestException,
        KeyError,
        ValueError,
    ) as error:
        logger.warning(
            f"Could not ask BloodHound which edition it is ({error}); "
            "using the Enterprise ingest route"
        )
        return None

    if edition != EDITION_COMMUNITY:
        logger.debug(
            f"BloodHound reports edition '{edition}'; using the Enterprise route"
        )
        return None

    logger.info(
        "BloodHound Community Edition detected; sending data through a file-upload job"
    )
    return client


def build_document(items: TDataItems, source_kind: str | None) -> dict:
    """Turn one batch of converted items into an OpenGraph document.

    The ``metadata`` section names the source kind. BloodHound uses it to register the source
    kind, which makes the data removable by source kind under Database Management, and to label
    the imported objects. It is left out when the collector declares no source kind.
    """
    nodes = []
    edges = []
    for item in items:
        if item["graph"]["entity_type"] == "node":
            nodes.append(item["graph"]["content"])
        elif item["graph"]["entity_type"] == "edge":
            edges.extend(item["graph"]["content"])

    document: dict = {"graph": {"nodes": nodes, "edges": edges}}
    if source_kind:
        document = {"metadata": {"source_kind": source_kind}, **document}
    return document


def community_edition_destination(
    client: BloodHoundClient, job_id: int, source_kind: str | None
) -> Destination:
    """Create a dlt destination that attaches every batch to one Community Edition job.

    Each batch becomes one gzip-compressed OpenGraph JSON file named after its table and a
    running number, for example ``users-0001.json``. That name is what BloodHound's File Ingest
    page shows. dlt may deliver batches from several threads at once, so the running number is
    protected by a lock.

    Args:
        client (BloodHoundClient): A signed-in Community Edition client.
        job_id (int): The file-upload job the converter started.
        source_kind (str | None): The collector's source kind, written into each file's metadata.
    """
    file_numbers = itertools.count(1)
    file_number_lock = threading.Lock()

    @dlt.destination(
        name="bloodhound_community",
        skip_dlt_columns_and_tables=True,
        batch_size=ITEMS_PER_FILE,
    )
    def attach_to_job(items: TDataItems, table: TTableSchema) -> None:
        with file_number_lock:
            file_number = next(file_numbers)
        file_name = f"{table['name']}-{file_number:04d}.json"

        body = gzip.compress(json.dumps(build_document(items, source_kind)).encode())
        logger.info(
            f"Attaching {len(items)} items to BloodHound job {job_id} as {file_name}"
        )
        client.upload_file_to_job(
            job_id=job_id,
            body=body,
            content_type=CONTENT_TYPE_JSON,
            file_name=file_name,
            content_encoding="gzip",
        )

    return attach_to_job()
