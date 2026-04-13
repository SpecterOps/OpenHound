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


class Collector(BasePipeline):
    def __init__(
        self,
        name: str,
        output_path: Path,
        resources: list[str] | None = None,
        progress: Progress = Progress.tqdm,
    ):
        self.name = name
        self.output_path = output_path
        self.resources = resources if resources else []
        self.progress = progress

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
            all_resources, write_disposition="replace", loader_file_format="jsonl"
        )


@dataclass
class CollectContext:
    pipeline: Collector
