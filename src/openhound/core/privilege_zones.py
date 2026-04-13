from pathlib import Path
from typing import Callable

import dlt
from dlt.common.pipeline import LoadInfo
from dlt.pipeline.pipeline import Pipeline

from openhound.core.pipeline import BasePipeline
from openhound.destinations.bloodhound.destination import Strategy, privilege_zones
from openhound.sources.bloodhound_config.source import Format, bloodhound_config


class PrivilegeZones(BasePipeline):
    def __init__(
        self,
        progress: str | Callable,
        strategy: Strategy = Strategy.skip,
    ):
        self.progress = progress
        self.strategy = strategy

    @property
    def pipeline(self) -> Pipeline:
        pipeline = dlt.pipeline(
            pipeline_name="bloodhound_pz",
            dataset_name="privilege_zone",
            destination=privilege_zones(strategy=self.strategy),
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
            privilege_zones=files, file_format=file_format
        ).with_resources("privilege_zone")
        return self._run(source)
