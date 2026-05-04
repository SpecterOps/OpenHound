import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import dlt
import duckdb
from dlt.common.pipeline import LoadInfo
from dlt.pipeline.pipeline import Pipeline

from openhound.core.logging import logger_override
from openhound.core.pipeline import BasePipeline
from openhound.core.progress import Progress
from openhound.sources.resource_files.source import resource_files

logger = logging.getLogger(__name__)


def run_transform(
    transform: Callable[..., None],
    con: duckdb.DuckDBPyConnection,
    *args,
    **kwargs,
) -> None:
    """A transformer helper function that handles DuckDB exceptions when generating a lookup"""
    try:
        transform(con, *args, **kwargs)

    except duckdb.CatalogException as err:
        logger.error(
            "DuckDB preprocessing transform '%s' failed due to missing table: %s",
            transform.__name__,
            err,
        )

    except duckdb.Error as err:
        logger.error(
            "DuckDB preprocessing transform '%s' failed: %s",
            transform.__name__,
            err,
        )


class PreProcessor(BasePipeline):
    def __init__(
        self,
        name: str,
        input_path: Path,
        output_file: Path,
        transformer: Callable[[duckdb.DuckDBPyConnection], None] | None = None,
        progress: Progress = Progress.tqdm,
    ):
        """Create a preprocessor that stores previously collected resources inside a DuckDB lookup database. The database can be used
        during the OpenGraph convertion for pre-processing.

        Args:
            name (str): Dataset name used for the DLT pipeline.
            input_path (Path): Path containing collected resource files.
            output_file (Path): DuckDB file path for persistent lookup storage.
            transformer (Callable, optional): Optional transformation function that takes a DuckDB connection and performs transformations.
            progress (Literal["tqdm", "log", "alive_progress"], optional): Progress backend. Log is preferred for production use and alive_progress for interactive use.
        """
        self.name = name
        self.progress = progress
        self.input_path = input_path
        self.output_file = output_file
        self.transformer = transformer

    @property
    def pipeline(self) -> Pipeline:
        """Build the DLT pipeline used to populate the lookup DuckDB database.

        Returns:
            Pipeline: Configured DLT pipeline.
        """
        duckdb_dest = dlt.destinations.duckdb(str(self.output_file))
        return dlt.pipeline(
            pipeline_name=f"{self.name}_preproc",
            destination=duckdb_dest,
            dataset_name=self.name,
            progress=self.progress,  # type: ignore
        )

    def run(
        self, resources: dict[str, str], filters: Callable | None = None
    ) -> LoadInfo:
        """Run DLT pipeline to load resources into DuckDB and apply optional transformations."""
        source = resource_files(self.input_path, resources=resources)
        logger_override.set_handler(self.name)
        result = self._run(source, write_disposition="replace")

        if self.transformer:
            # Apply transformations to the loaded data
            con = duckdb.connect(str(self.output_file))
            try:
                self.transformer(con)
            except duckdb.CatalogException as err:
                logger.error(
                    "DuckDB preprocessing failed due to missing table: %s", err
                )
            except duckdb.Error as err:
                logger.error("DuckDB preprocessing failed: %s", err)
            finally:
                con.close()

        return result


@dataclass
class PreProcContext:
    pipeline: PreProcessor
