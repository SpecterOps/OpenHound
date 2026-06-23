import logging
from enum import Enum
from pathlib import Path
from typing import Annotated, Callable, List

import dlt
import duckdb
import typer
from dlt.common.libs import pydantic as dlt_pydantic
from dlt.common.pipeline import LoadInfo
from dlt.extract.resource import DltResource
from dlt.extract.source import DltSource

from openhound.cli.collect import collect
from openhound.cli.convert import convert
from openhound.cli.preproc import preprocess
from openhound.core import validate
from openhound.core.asset import BaseAsset, EdgeDef, NodeDef
from openhound.core.collect import CollectContext, Collector
from openhound.core.convert import ConvertContext, Converter, Method
from openhound.core.models.extension import Extension
from openhound.core.preproc import PreProcContext, PreProcessor
from openhound.core.progress import Progress
from openhound.core.resources import safe_resource_wrapper

logger = logging.getLogger(__name__)

OutputPath = Annotated[
    Path,
    typer.Argument(
        exists=False,
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
    ),
]

InputPath = Annotated[
    Path,
    typer.Argument(
        exists=True, file_okay=True, dir_okay=True, readable=True, resolve_path=True
    ),
]

DEFAULT_LOOKUP_FILE = Path("lookup.duckdb")


class Contract(str, Enum):
    evolve = "evolve"
    freeze = "freeze"
    discard_row = "discard_row"


class OpenHound:
    def __init__(self, name: str, source_kind: str, help: str = "OpenGraph collector"):
        dlt_pydantic.create_list_model = validate.create_list_model
        dlt_pydantic._classify_validation_errors = validate._classify_validation_errors

        self.name = name
        self.source_kind = source_kind
        self.help = help

        # Extension metadata is loaded and validated when the extension is loaded in the CollectorManager
        self.metadata: Extension | None = None
        # Store the collect/convert/preproc methods for this source
        self.collector: Callable | None = None
        self.converter: Callable | None = None
        self.preprocessor: Callable | None = None

        # Store DLT resources/transformers for this source to be used when building the DLT pipeline
        self.dlt_source: DltSource | None = None
        self.dlt_resources: list[DltResource] = []
        self.dlt_transformers: list[DltResource] = []

        # Store the graph definitions for this source
        self.assets: list[BaseAsset] = []
        self.nodes: list[NodeDef] = []
        self.edges: list[EdgeDef] = []

    def collect(
        self,
        help: str = "OpenGraph collect pipeline",
        **kwargs,
    ):
        """Register a Typer CLI command that collects resources and stores them (filtered) on disk.

        Args:u
            name (str): Dataset name used for the DLT pipeline.
            help (str, optional): Typer CLI help text.
            progress (Literal["tqdm", "log", "alive_progress"], optional): Progress backend. Log is preferred for production use and alive_progress for interactive use.
        """

        def decorator(func: Callable):
            def wrapper(
                output_path: OutputPath,
                resources: List[str] = typer.Argument(None),
                progress: Progress = typer.Option(
                    Progress.tqdm, help="Select progress tracker option"
                ),
                tables_contract: Annotated[
                    Contract,
                    typer.Option(
                        help="DLT contract applied when data contains newly seen resources/tables previously not collected",
                    ),
                ] = Contract.evolve,
                columns_contract: Annotated[
                    Contract,
                    typer.Option(
                        help="DLT contract applied when data contains values/keys not found in the Pydantic model",
                    ),
                ] = Contract.evolve,
                data_type_contract: Annotated[
                    Contract,
                    typer.Option(
                        help="DLT contract applied when fields do not match the data types defined in the Pydantic model",
                    ),
                ] = Contract.discard_row,
            ) -> LoadInfo | None:
                schema_contract = {
                    "tables": tables_contract,
                    "columns": columns_contract,
                    "data_type": data_type_contract,
                }
                collector = Collector(
                    name=self.name,
                    output_path=output_path,
                    resources=resources,
                    progress=progress,
                    schema_contract=schema_contract,
                )

                ctx = CollectContext(pipeline=collector)
                source_method: DltSource = func(ctx)
                if source_method:
                    return collector.run(source_method)

            logger.debug(f"Registering collect command for {self.name}")
            self.collector = wrapper
            decorated = collect.command(name=self.name, help=help)(wrapper)
            return decorated

        return decorator

    def convert(
        self,
        lookup: Callable | None = None,
        help: str = "OpenGraph convert pipeline",
        **typer_kwargs,
    ):
        """Register a Typer CLI command that converts collected resources to OpenGraph nodes and edges.

        Args:
            name (str): Dataset name used for the DLT pipeline.
            lookup (Callable): Lookup helper from a DuckDB connection.
            help (str, optional): Typer CLI help text.
            progress (Literal["tqdm", "log", "alive_progress"], optional): Progress backend. Log is preferred for producteion use and alive_progress for interactive use.
        """

        def decorator(func: Callable):
            def run_convert(
                input_path: InputPath,
                output_path: Path = Path("/tmp/openhound"),
                lookup_file: Path = DEFAULT_LOOKUP_FILE,
                progress: Progress = Progress.tqdm,
                method: Method = Method.write,
            ) -> LoadInfo:
                lookup_session = None
                if lookup:
                    client = duckdb.connect(str(lookup_file), read_only=True)
                    lookup_session = lookup(client)

                if isinstance(progress, str):
                    progress = Progress(progress)

                converter = Converter(
                    name=self.name,
                    source_kind=self.source_kind,
                    input_path=input_path,
                    output_path=output_path,
                    lookup=lookup_session,
                    progress=progress,
                    method=method,
                )

                source_method, extra_context = func(
                    ConvertContext(
                        input_path=input_path,
                        output_path=output_path,
                        lookup=lookup_session,
                        pipeline=converter,
                    )
                )
                return converter.run(
                    source_method,
                    graph_resources=self.assets,
                    extra_context=extra_context,
                )

            def wrapper(
                input_path: InputPath,
                output_path: Annotated[
                    Path,
                    typer.Argument(
                        exists=False,
                        file_okay=False,
                        dir_okay=True,
                        resolve_path=True,
                        help="Output path to write OpenGraph JSON files",
                    ),
                ],
                # resources: List[str] = typer.Argument(None),
                progress: Progress = typer.Option(
                    Progress.tqdm, help="Select progress tracker option"
                ),
                lookup_file: Annotated[
                    Path,
                    typer.Option(
                        file_okay=True,
                        dir_okay=False,
                        readable=True,
                        resolve_path=True,
                        help="DuckDB lookup file path",
                    ),
                ] = DEFAULT_LOOKUP_FILE,
            ) -> LoadInfo:
                return run_convert(
                    input_path=input_path,
                    output_path=output_path,
                    lookup_file=lookup_file,
                    progress=progress,
                    method=Method.write,
                )

            logger.debug(f"Registering convert command for {self.name}")
            self.converter = run_convert
            decorated = convert.command(name=self.name, help=help, **typer_kwargs)(
                wrapper
            )
            return decorated

        return decorator

    def preproc(
        self,
        transformer: Callable[[any], None] | None = None,
        help: str = "OpenGraph preprocessing pipeline",
        **typer_kwargs,
    ):
        """Register a Typer CLI command that performs optional preprocessing and builds lookup data for a source.

        Args:
            transformer (Callable, optional): Optional transformation function that takes a DuckDB connection and performs transformations.
            help (str, optional): CLI help text.
            progress (Literal["tqdm", "log", "alive_progress"], optional): Progress backend. Log is preferred for production use and alive_progress for interactive use.
        """

        def decorator(func: Callable):
            self.preprocessor = func

            def wrapper(
                input_path: InputPath,
                output_file: Annotated[
                    Path,
                    typer.Argument(
                        file_okay=True,
                        dir_okay=False,
                        readable=True,
                        resolve_path=True,
                    ),
                ] = DEFAULT_LOOKUP_FILE,
                progress: Progress = typer.Option(
                    Progress.tqdm, help="Select progress tracker option"
                ),
            ) -> LoadInfo:
                preprocessor = PreProcessor(
                    name=self.name,
                    input_path=input_path,
                    output_file=output_file,
                    progress=progress,
                    transformer=transformer,
                )

                resource_list = func(PreProcContext(pipeline=preprocessor))
                return preprocessor.run(resources=resource_list)

            logger.debug(f"Registering preproc command for {self.name}")
            self.preprocessor = wrapper
            decorated = preprocess.command(name=self.name, help=help, **typer_kwargs)(
                wrapper
            )
            return decorated

        return decorator

    def transformer(
        self,
        *dlt_args,
        **dlt_kwargs,
    ):
        """Decorator to register a DLT transformer with added exception handling."""

        def decorator(func: Callable) -> DltResource:
            transformer_name = dlt_kwargs.get("name", func.__name__)
            safe_func = safe_resource_wrapper(func, transformer_name)
            decorated = dlt.transformer(safe_func, *dlt_args, **dlt_kwargs)
            self.dlt_resources.append(decorated)
            return decorated  # type: ignore

        logger.debug(f"Registering transformer for {self.name}")
        return decorator

    def resource(
        self,
        *dlt_args,
        **dlt_kwargs,
    ):
        """Decorator to register a DLT resource with added exception handling."""

        def decorator(func: Callable) -> DltResource:
            resource_name = dlt_kwargs.get("name", func.__name__)
            safe_func = safe_resource_wrapper(func, resource_name)
            decorated = dlt.resource(safe_func, *dlt_args, **dlt_kwargs)
            self.dlt_resources.append(decorated)  # type: ignore
            return decorated  # type: ignore

        logger.debug(f"Registering resource for {self.name}")
        return decorator

    def source(
        self,
        *dlt_args,
        **dlt_kwargs,
    ):
        """Decorator to register a DLT source with added exception handling.

        Args:
            name (str): Dataset name used for the DLT pipeline.

        """

        def decorator(func: Callable) -> Callable:
            decorated = dlt.source(func, *dlt_args, **dlt_kwargs)
            self.dlt_source = decorated  # type: ignore
            return decorated  # type: ignore

        logger.debug(f"Registering source for {self.name}")
        return decorator

    def asset(
        self,
        node: NodeDef | None = None,
        edges: list[EdgeDef] | None = None,
        description: str = "Resource model for OpenGraph",
    ):
        """Decorator to register a resource class and its graph definitions (nodes/edges). This is used to automatically
        generate documentation for each unique resource and implement rules/warnings when nodes/edges are returned
        which are not declared.

        Args:
            name (str): Resource name
            description (str, optional): Description of the resource
        """

        def decorator(func: BaseAsset):
            self.assets.append(func)
            if node:
                self.nodes.append(node)

            if edges:
                for edge in edges:
                    self.edges.append(edge)

            return func

        logger.debug(f"Registering asset for {self.name}")
        return decorator
