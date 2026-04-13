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


@dlt.source(name="opengraph", max_table_nesting=0)
def opengraph(
    graph_resources: list[GraphResource],
    bucket_url: str,
    lookup: LookupManager,
    extras: dict | None = None,
    batch_size: int = 150,
):

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

        @dlt.transformer(parallelized=False, name=table_name, columns=GraphContent)
        def generate_graph(resources, model, apply_context: Callable | None = None):
            for resource in resources:
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

                edge_parts = []
                for edge in parsed_resource.edges:
                    edge_parts.append(asdict(edge))
                    if len(edge_parts) >= batch_size:
                        yield {"graph": {"content": edge_parts, "entity_type": "edge"}}
                        edge_parts = []

                if edge_parts:
                    yield {"graph": {"content": edge_parts, "entity_type": "edge"}}

        yield reader | generate_graph(
            model=graph_resource.model, apply_context=apply_context
        )
