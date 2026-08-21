from dataclasses import asdict, dataclass
from typing import Callable

import dlt
from dlt.sources.filesystem import filesystem as filesystemsource
from dlt.sources.filesystem import read_jsonl

from openhound.core.asset import BaseAsset
from openhound.core.lookup import LookupManager

from .entries import GraphContent


@dataclass
class GraphResource:
    table: str
    model: BaseAsset


DEFAULT_EDGE_BATCH_SIZE = 150

# Each wrapper holds up to batch_size edges; scale DLT's item-count writer
# buffer so buffered edges stay bounded regardless of batch size.
DLT_BUFFERED_EDGE_BUDGET = 50_000
DLT_DEFAULT_BUFFER_MAX_ITEMS = 5_000


def writer_buffer_max_items(batch_size: int = DEFAULT_EDGE_BATCH_SIZE) -> int:
    if batch_size < 1:
        raise ValueError(f"batch_size must be >= 1, got {batch_size}")
    return min(
        DLT_DEFAULT_BUFFER_MAX_ITEMS,
        max(1, DLT_BUFFERED_EDGE_BUDGET // batch_size),
    )


@dlt.source(name="opengraph", max_table_nesting=0)
def opengraph(
    graph_resources: list[GraphResource],
    bucket_url: str,
    lookup: LookupManager,
    extras: dict | None = None,
    batch_size: int = DEFAULT_EDGE_BATCH_SIZE,
):
    if batch_size < 1:
        raise ValueError(f"batch_size must be >= 1, got {batch_size}")

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
            | read_jsonl()
        )

        @dlt.resource(name=table_name, columns=GraphContent)
        def generate_graph(
            reader=reader,
            model=graph_resource.model,
            apply_context: Callable | None = apply_context,
        ):
            # One generator consumes the whole reader stream, so the edge
            # accumulator spans the entire table (across chunks and files) and
            # the final partial batch flushes once, giving exactly
            # ceil(total_edges / batch_size) wrappers in encounter order. Tradeoff:
            # a failure re-extracts the whole table rather than resuming mid-chunk.
            edge_parts = []
            for resource in reader:
                parsed_resource = model(**resource)
                if apply_context:
                    apply_context(parsed_resource)

                as_node = parsed_resource.as_node
                if as_node:
                    yield {
                        "graph": {
                            "content": asdict(as_node),
                            "entity_type": "node",
                        },
                    }

                for edge in parsed_resource.edges:
                    edge_parts.append(asdict(edge))
                    if len(edge_parts) >= batch_size:
                        yield {"graph": {"content": edge_parts, "entity_type": "edge"}}
                        edge_parts = []

            if edge_parts:
                yield {"graph": {"content": edge_parts, "entity_type": "edge"}}

        yield generate_graph()
