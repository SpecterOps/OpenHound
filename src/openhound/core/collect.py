import logging
from dataclasses import dataclass
from pathlib import Path

import dlt
from dlt.common.pipeline import LoadInfo
from dlt.destinations import filesystem
from dlt.extract.source import DltSource
from dlt.pipeline.pipeline import Pipeline

from openhound.core.logging import logger_override
from openhound.core.pipeline import BasePipeline
from openhound.core.progress import Progress

logger = logging.getLogger(__name__)

DEFAULT_SCHEMA_CONTRACT = {
    "tables": "evolve",
    "columns": "evolve",
    "data_type": "evolve",
}


class Collector(BasePipeline):
    def __init__(
        self,
        name: str,
        output_path: Path,
        resources: list[str] | None = None,
        progress: Progress = Progress.tqdm,
        schema_contract: dict | None = None,
    ):
        self.name = name
        self.output_path = output_path
        self.resources = resources if resources else []
        self.progress = progress
        self.schema_contract = (
            schema_contract if schema_contract is not None else DEFAULT_SCHEMA_CONTRACT
        )

    @property
    def pipeline(self) -> Pipeline:
        fs_dest = filesystem(
            bucket_url=str(self.output_path),
        )
        pipeline = dlt.pipeline(
            pipeline_name=f"{self.name}_collect",
            destination=fs_dest,
            dataset_name=self.name,
            progress=self.progress.value,
        )

        return pipeline

    def run(self, source_object: DltSource, **kwargs) -> LoadInfo:
        """Run the DLT pipeline to start collecting resource.

        Args:
            source_object (DltSource): The DLT source returning the DLT resources/transformers
        """
        logger.info(f"Starting collector '{self.name}'")
        self.output_path.mkdir(parents=True, exist_ok=True)
        all_resources = source_object
        if self.resources:
            all_resources = all_resources.with_resources(*self.resources)

        logger_override.set_handler(self.name)
        return self._run(
            all_resources,
            write_disposition="replace",
            loader_file_format="jsonl",
            schema_contract=self.schema_contract,
        )


@dataclass
class CollectContext:
    pipeline: Collector
