import logging

import dlt
from dlt.common import json
from dlt.common.schema import TTableSchema
from dlt.common.typing import TDataItems

from openhound.core.clients.bloodhound_enterprise import BloodHoundEnterprise

logger = logging.getLogger(__name__)


@dlt.destination(
    name="bloodhoundenterprise", skip_dlt_columns_and_tables=True, batch_size=1000
)
def ingest(
    items: TDataItems,
    table: TTableSchema,
    url: str = dlt.secrets.value,
    token_id: str = dlt.secrets.value,
    token_key: str = dlt.secrets.value,
    source_kind: str = dlt.config.value,
):

    client = BloodHoundEnterprise(token_key=token_key, token_id=token_id, bhe_uri=url)

    nodes = []
    edges = []

    logger.info(f"Processing {len(items)} items for BloodHound Enterprise ingest")
    for item in items:
        if item["graph"]["entity_type"] == "node":
            nodes.append(item["graph"]["content"])
        if item["graph"]["entity_type"] == "edge":
            edges.extend(item["graph"]["content"])

    client.ingest(
        json.dumps(
            {
                "graph": {"nodes": nodes, "edges": edges},
                "metadata": {"source_kind": source_kind},
            }
        )
    )
    logger.info("Graph ingested to BloodHound Enterprise")
