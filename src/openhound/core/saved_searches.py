from pathlib import Path

import dlt
from dlt.common.pipeline import LoadInfo
from dlt.pipeline.pipeline import Pipeline

from openhound.core.pipeline import BasePipeline
from openhound.core.progress import Progress
from openhound.destinations.bloodhound.destination import (
    Permissions,
    Strategy,
    saved_searches,
)
from openhound.sources.bloodhound_config.source import Format, bloodhound_config


class SavedSearches(BasePipeline):
    def __init__(
        self,
        progress: str | Progress,
        strategy: Strategy = Strategy.skip,
        permissions: Permissions = Permissions.public,
    ):
        self.strategy = strategy
        self.permissions = permissions
        self.progress = progress

    @property
    def pipeline(self) -> Pipeline:
        pipeline = dlt.pipeline(
            pipeline_name="bloodhound_searches",
            dataset_name="saved_searches",
            destination=saved_searches(
                strategy=self.strategy, permissions=self.permissions
            ),
            progress=self.progress,  # type: ignore
        )
        return pipeline

    def run(
        self,
        files: list[Path],
        file_format: Format = Format.json,
        **kwargs,
    ) -> LoadInfo:

        source = bloodhound_config(
            saved_queries=files, file_format=file_format
        ).with_resources("saved_search")
        return self._run(source)
