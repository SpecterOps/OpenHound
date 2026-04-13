import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable

import dlt
from dlt.common.pipeline import LoadInfo
from dlt.extract.source import DltSource
from dlt.pipeline.pipeline import Pipeline

from openhound.core.asset import BaseAsset
from openhound.core.clients.bloodhound import BloodHound
from openhound.core.logging import logger_override
from openhound.core.lookup import LookupManager
from openhound.core.pipeline import BasePipeline
from openhound.core.progress import Progress
from openhound.destinations.bloodhound_enterprise.destination import ingest
from openhound.destinations.opengraph.destination import opengraph_file
from openhound.sources.opengraph.source import GraphResource, opengraph

logger = logging.getLogger(__name__)


@dataclass
class Credentials:
    url: str
    token_key: str
    token_id: str


class Method(str, Enum):
    write = "write"
    ingest = "ingest"


class Converter(BasePipeline):
    def __init__(
        self,
        name: str,
        input_path: Path,
        lookup: LookupManager,
        output_path: Path,
        source_kind: str,
        progress: Progress = Progress.tqdm,
        method: Method = Method.write,
    ):
        self.name = name
        self.input_path = input_path
        self.output_path = output_path
        self.source_kind = source_kind
        self.lookup = lookup
        self.progress = progress
        self.method = method
        self.client: BloodHound | None = None
        self.upload_id: int | None = None

    @property
    def _credentials(self) -> Credentials:
        return Credentials(
            url=dlt.secrets["destination.bloodhound.url"],
            token_id=dlt.secrets["destination.bloodhound.token_id"],
            token_key=dlt.secrets["destination.bloodhound.token_key"],
        )

    @property
    def pipeline(self) -> Pipeline:
        if self.method == Method.ingest:
            logger.debug(
                "Initializing BloodHound Enterprise client for converter ingest method"
            )
            dest = ingest(source_kind=self.source_kind)

        else:
            logger.debug("Using file output method for converter")
            dest = opengraph_file(
                output_path=str(self.output_path), source_kind=self.source_kind
            )

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
        result = self._run(
            opengraph(
                valid_resources,
                lookup=self.lookup,
                bucket_url=str(self.input_path),
                extras=extra_context,
            )
        )

        logger.info(f"Converter run completed with method '{self.method}'")
        return result


@dataclass
class ConvertContext:
    input_path: Path
    output_path: Path
    pipeline: Converter
    lookup: Callable
