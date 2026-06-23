from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, Optional, Type

from dlt import Schema
from dlt.common.configuration.specs import BaseConfiguration
from dlt.common.schema.typing import (
    TAnySchemaColumns,
    TFileFormat,
    TSchemaContract,
    TTableFormat,
    TTableReferenceParam,
    TWriteDisposition,
    TWriteDispositionConfig,
)
from dlt.common.typing import (
    AnyFun,
    Concatenate,  # type: ignore
    ParamSpec,  # type: ignore
    TColumnNames,
    TDataItem,
    TTableHintTemplate,
    TTableNames,
)
from dlt.extract.hints import TResourceNestedHints
from dlt.extract.incremental import TIncrementalConfig
from dlt.extract.resource import DltResource, TUnboundDltResource
from dlt.extract.source import DltSource

from openhound.core.asset import EdgeDef, NodeDef
from openhound.core.models.extension import Extension

TResourceFunParams = ParamSpec("TResourceFunParams")

class Contract(str, Enum):
    evolve = "evolve"
    freeze = "freeze"
    discard_value = "discard_value"
    discard_row = "discard_row"

@dataclass
class Asset:
    node: Enum
    description: str
    model: Callable

class OpenHound:
    name: str
    source_kind: str
    help: str
    metadata: Extension | None
    collector: Callable | None
    converter: Callable | None
    preprocessor: Callable | None
    dlt_source: DltSource | None
    dlt_resources: list[DltResource]
    dlt_transformers: list[DltResource]
    table_contract: Contract
    data_type_contract: Contract
    columns_contract: Contract
    assets: list[Asset]
    nodes: list[NodeDef]
    edges: list[EdgeDef]

    def __init__(
        self, name: str, source_kind: str, help: str = "OpenGraph collector"
    ): ...
    def icons(self, color: str, help: str = "BloodHound icons sync"): ...
    def queries(self, help: str = "BloodHound icons sync"): ...
    def preproc(
        self,
        transformer: Callable | None = None,
        help: str = "OpenGraph preprocessing pipeline",
        **typer_kwargs,
    ): ...
    def collect(
        self,
        help: str = "OpenGraph collect pipeline",
        **kwargs,
    ): ...
    def convert(
        self,
        lookup: Callable | None = None,
        help: str = "OpenGraph convert pipeline",
        **typer_kwargs,
    ): ...
    def source(
        self,
        func: Optional[AnyFun] = None,
        name: str | None = None,
        section: str | None = None,
        max_table_nesting: int | None = None,
        root_key: bool | None = None,
        schema: Schema | None = None,
        schema_contract: TSchemaContract | None = None,
        spec: Type[BaseConfiguration] | None = None,
        parallelized: bool = False,
        _impl_cls: type[DltSource] = DltSource,
    ) -> Any: ...
    def defer(self): ...
    def resource(
        self,
        data: Optional[Any] = None,
        name: TTableHintTemplate[str] | None = None,
        table_name: TTableHintTemplate[str] | None = None,
        max_table_nesting: int | None = None,
        write_disposition: TTableHintTemplate[TWriteDispositionConfig] | None = None,
        columns: TTableHintTemplate[TAnySchemaColumns] | None = None,
        primary_key: TTableHintTemplate[TColumnNames] | None = None,
        merge_key: TTableHintTemplate[TColumnNames] | None = None,
        schema_contract: TTableHintTemplate[TSchemaContract] | None = None,
        table_format: TTableHintTemplate[TTableFormat] | None = None,
        file_format: TTableHintTemplate[TFileFormat] | None = None,
        references: TTableHintTemplate[TTableReferenceParam] | None = None,
        nested_hints: Optional[
            TTableHintTemplate[Dict[TTableNames, TResourceNestedHints]]
        ] = None,
        selected: bool = True,
        spec: Type[BaseConfiguration] | None = None,
        parallelized: bool = False,
        incremental: Optional[TIncrementalConfig] = None,
        _impl_cls: Type[DltResource] = DltResource,
        section: Optional[TTableHintTemplate[str]] = None,
        _base_spec: Type[BaseConfiguration] = BaseConfiguration,
        standalone: bool | None = None,
        data_from: TUnboundDltResource | None = None,
    ) -> Callable[[Callable[..., Any]], DltResource]: ...
    def transformer(
        self,
        f: Optional[Callable[Concatenate[TDataItem, TResourceFunParams], Any]] = None,
        data_from: TUnboundDltResource = DltResource.Empty,
        name: TTableHintTemplate[str] | None = None,
        table_name: TTableHintTemplate[str] | None = None,
        max_table_nesting: int | None = None,
        write_disposition: TTableHintTemplate[TWriteDisposition] | None = None,
        columns: TTableHintTemplate[TAnySchemaColumns] | None = None,
        primary_key: TTableHintTemplate[TColumnNames] | None = None,
        merge_key: TTableHintTemplate[TColumnNames] | None = None,
        schema_contract: TTableHintTemplate[TSchemaContract] | None = None,
        table_format: TTableHintTemplate[TTableFormat] | None = None,
        file_format: TTableHintTemplate[TFileFormat] | None = None,
        references: TTableHintTemplate[TTableReferenceParam] | None = None,
        nested_hints: Optional[
            TTableHintTemplate[Dict[TTableNames, TResourceNestedHints]]
        ] = None,
        selected: bool = True,
        spec: Type[BaseConfiguration] | None = None,
        parallelized: bool = False,
        section: Optional[TTableHintTemplate[str]] = None,
        standalone: bool | None = None,
        _impl_cls: Type[DltResource] = DltResource,
    ) -> Callable[[Callable[..., Any]], DltResource]: ...
    def asset(
        self,
        node: NodeDef | None = None,
        edges: list[EdgeDef] | None = None,
        description: str = "Resource model for OpenGraph",
    ): ...
