import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable

import dlt
import requests
from dlt.common.pipeline import LoadInfo
from dlt.extract.source import DltSource
from dlt.pipeline.pipeline import Pipeline

from openhound.core.asset import BaseAsset
from openhound.core.clients.bloodhound import BloodHoundClient, BloodHoundHTTPError
from openhound.core.community_ingest import community_edition_destination
from openhound.core.logging import logger_override
from openhound.core.lookup import LookupManager
from openhound.core.pipeline import BasePipeline
from openhound.core.progress import Progress
from openhound.destinations.bloodhound_enterprise.destination import ingest
from openhound.destinations.opengraph.destination import opengraph_file
from openhound.sources.opengraph.source import GraphResource, opengraph

logger = logging.getLogger(__name__)


class Method(str, Enum):
    """Where the converter sends its output.

    ``write`` saves OpenGraph JSON files to the output folder. ``ingest`` sends the data
    straight to BloodHound: through ``/api/v2/ingest`` for Enterprise, or through one
    file-upload job for Community Edition (see :mod:`openhound.core.community_ingest`).
    """

    write = "write"
    ingest = "ingest"


class Converter(BasePipeline):
    def __init__(
        self,
        name: str,
        input_path: Path,
        lookup: LookupManager,
        output_path: Path,
        source_kind: str | None = None,
        progress: Progress = Progress.tqdm,
        method: Method = Method.write,
        community_edition_client: BloodHoundClient | None = None,
    ):
        """
        Args:
            community_edition_client: Set only by the ``convert`` command line when ``ingest``
                is chosen and the configured BloodHound is Community Edition. Without it,
                ``ingest`` takes the Enterprise route, as the Enterprise scheduler always does.
        """
        self.name = name
        self.input_path = input_path
        self.output_path = output_path
        self.source_kind = source_kind
        self.lookup = lookup
        self.progress = progress
        self.method = method
        self.community_edition_client = community_edition_client
        # The Community Edition file-upload job for the current run, set by run().
        self.community_edition_job_id: int | None = None

    @property
    def sends_to_community_edition(self) -> bool:
        return (
            self.method == Method.ingest and self.community_edition_client is not None
        )

    @property
    def pipeline(self) -> Pipeline:
        if self.sends_to_community_edition:
            assert self.community_edition_client is not None
            assert self.community_edition_job_id is not None
            logger.debug(
                "Using BloodHound Community Edition file-upload job for ingest"
            )
            dest = community_edition_destination(
                client=self.community_edition_client,
                job_id=self.community_edition_job_id,
                source_kind=self.source_kind,
            )

        elif self.method == Method.ingest:
            logger.debug(
                "Initializing BloodHound Enterprise client for converter ingest method"
            )
            dest = ingest()

        else:
            logger.debug("Using file output method for converter")
            dest = opengraph_file(output_path=str(self.output_path))

        pipeline = dlt.pipeline(
            pipeline_name=f"{self.name}_convert",
            dataset_name=self.name,
            destination=dest,
            progress=self.progress.value,
        )
        return pipeline

    def run(
        self,
        source_object: DltSource,
        graph_resources: list[BaseAsset],
        extra_context: dict,
        **kwargs,
    ) -> LoadInfo:
        logger.info(f"Running converter with method '{self.method}'")
        if self.method == Method.write:
            self.output_path.mkdir(parents=True, exist_ok=True)
        # TODO: Add resource filter
        source_models = {
            dlt_resource.validator.model: dlt_resource.table_name  # pyright: ignore[reportAttributeAccessIssue]
            for dlt_resource in source_object.resources.values()
            if dlt_resource.validator and hasattr(dlt_resource.validator, "model")
        }

        valid_resources = []
        for graph_resource in graph_resources:
            if graph_resource in source_models:
                table_name = source_models[graph_resource]
                valid_resources.append(
                    GraphResource(table=str(table_name), model=graph_resource)
                )

        logger_override.set_handler(self.name)
        source = opengraph(
            valid_resources,
            lookup=self.lookup,
            bucket_url=str(self.input_path),
            extras=extra_context,
            source_kind=self.source_kind,
        )

        if self.sends_to_community_edition:
            result = self._run_into_community_edition_job(source)
        else:
            result = self._run(source)

        logger.info(f"Converter run completed with method '{self.method}'")
        return result

    def _run_into_community_edition_job(self, source: DltSource) -> LoadInfo:
        """Run the conversion inside one Community Edition file-upload job.

        The job is opened before the first batch and always ended afterwards, even when the
        conversion fails part-way. Ending it makes BloodHound process whatever arrived; an
        open job would otherwise sit unfinished on the File Ingest page, because BloodHound
        offers no way to cancel one. BloodHound runs its analysis once, after the job ends.
        """
        assert self.community_edition_client is not None
        job = self.community_edition_client.start_file_upload_job()
        self.community_edition_job_id = job.id
        logger.info(f"Started BloodHound file-upload job {job.id}")

        try:
            result = self._run(source)
        except BaseException:
            # The conversion failed. Still end the job, but never let a second failure here
            # hide the original error, which is the one the person needs to see.
            try:
                self.community_edition_client.end_file_upload_job(job.id)
                logger.info(
                    f"Ended BloodHound file-upload job {job.id} after a failure"
                )
            except (BloodHoundHTTPError, requests.RequestException) as end_error:
                logger.error(
                    f"Could not end BloodHound file-upload job {job.id}: {end_error}"
                )
            raise

        self.community_edition_client.end_file_upload_job(job.id)
        logger.info(
            f"Ended BloodHound file-upload job {job.id}. BloodHound now processes "
            "the data; watch its progress under Administration > File Ingest."
        )
        return result


@dataclass
class ConvertContext:
    input_path: Path
    output_path: Path
    pipeline: Converter
    lookup: Callable
