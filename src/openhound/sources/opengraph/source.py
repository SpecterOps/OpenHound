from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass

import dlt
from dlt.sources.filesystem import filesystem as filesystemsource
from dlt.sources.filesystem import read_jsonl

from openhound.core.asset import BaseAsset
from openhound.core.lookup import LookupManager

from .entries import GraphContent

# DLT page boundary; partial pages flush per input file.
READ_JSONL_PAGE_SIZE = 1000


@dataclass
class GraphResource:
    table: str
    model: BaseAsset


def _generate_graph_content(
    resources: Iterable[dict],
    model: type[BaseAsset],
    batch_size: int,
    apply_context: Callable | None = None,
):
    """Convert one DLT page into bounded OpenGraph batches."""
    edge_parts = []

    def serialize(content):
        if hasattr(content, "model_dump"):
            return content.model_dump()
        return asdict(content)

    for resource in resources:
        parsed_resource = model(**resource)
        if apply_context:
            apply_context(parsed_resource)

        as_node = parsed_resource.as_node
        if as_node:
            yield {
                "graph": {
                    "content": serialize(as_node),
                    "entity_type": "node",
                },
            }

        for edge in parsed_resource.edges or []:
            edge_parts.append(serialize(edge))
            if len(edge_parts) == batch_size:
                yield {"graph": {"content": edge_parts, "entity_type": "edge"}}
                edge_parts = []

    if edge_parts:
        yield {"graph": {"content": edge_parts, "entity_type": "edge"}}


@dlt.source(name="opengraph", max_table_nesting=0)
def opengraph(
    graph_resources: list[GraphResource],
    bucket_url: str,
    lookup: LookupManager,
    extras: dict | None = None,
    batch_size: int = 150,
):
    if batch_size <= 0:
        raise ValueError("batch_size must be greater than zero")

    def apply_context(obj):
        obj._lookup = lookup
        obj._extras = extras

    for graph_resource in graph_resources:
        table_name = f"{graph_resource.model.__name__.lower()}_fs"
        reader = (
            filesystemsource(
                bucket_url=bucket_url,
                file_glob=f"{graph_resource.table}/**/*.jsonl.gz",
            )
            | read_jsonl(chunksize=READ_JSONL_PAGE_SIZE)
        )

        @dlt.transformer(parallelized=False, name=table_name, columns=GraphContent)
        def generate_graph(resources, model, apply_context: Callable | None = None):
            yield from _generate_graph_content(
                resources, model, batch_size, apply_context
            )

        yield reader | generate_graph(
            model=graph_resource.model, apply_context=apply_context
        )
