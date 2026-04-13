import logging
from collections import defaultdict
from pathlib import Path

import dlt
from dlt.common import json
from dlt.common.schema import TTableSchema
from dlt.common.typing import TDataItems

logger = logging.getLogger(__name__)

DEST_PART: defaultdict[str, int] = defaultdict(int)


@dlt.destination(skip_dlt_columns_and_tables=True, batch_size=1000)
def opengraph_file(
    items: TDataItems,
    table: TTableSchema,
    output_path: str = dlt.config.value,
    source_kind: str = dlt.config.value,
):

    table_name = table.get("name") or "opengraph"
    DEST_PART[table_name] += 1

    nodes = []
    edges = []
    logger.debug(
        f"Processing {len(items)} items for OpenGraph file output (part {DEST_PART[table_name]})"
    )
    for item in items:
        if item["graph"]["entity_type"] == "node":
            nodes.append(item["graph"]["content"])
        if item["graph"]["entity_type"] == "edge":
            edges.extend(item["graph"]["content"])

    file_name = f"{table_name}-{DEST_PART[table_name]}.json"
    output_dir = Path(output_path)
    file_path = output_dir / file_name

    with file_path.open("w", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "graph": {"nodes": nodes, "edges": edges},
                    "metadata": {"source_kind": source_kind},
                }
            ),
        )
