import logging
from collections import defaultdict
from enum import Enum

import dlt
from dlt.common import json
from dlt.common.schema import TTableSchema
from dlt.common.typing import TDataItems

from openhound.core.clients.bloodhound import BloodHoundJWT

logger = logging.getLogger(__name__)


DEST_PART: defaultdict[str, int] = defaultdict(int)


class Strategy(str, Enum):
    skip = "skip"
    overwrite = "overwrite"


class Permissions(str, Enum):
    public = "public"
    private = "private"


@dlt.destination(name="bloodhound", skip_dlt_columns_and_tables=True, batch_size=5)
def saved_searches(
    items: TDataItems,
    table: TTableSchema,
    url: str = dlt.secrets.value,
    token: str = dlt.secrets.value,
    strategy: Strategy = Strategy.skip,
    permissions: Permissions = Permissions.public,
):

    client = BloodHoundJWT(token=token, base_uri=url)
    logger.info(f"Processing {len(items)} queries for BloodHound saved search upload")
    existing_searches = {query.name: query.id for query in client.saved_queries.data}
    for query in items:
        query_name = query["name"]
        if query_name not in existing_searches:
            logger.info(
                f"Saved search '{query_name}' does not exist. Creating new saved search in BloodHound."
            )
            create_query = client.create_saved_query(json.dumps(query))
            if permissions == Permissions.public:
                client.set_query_permissions(create_query.data.id, {"public": True})

        elif strategy == Strategy.overwrite:
            logger.warning(
                f"Saved search '{query_name}' already exists in BloodHound. Overwriting as per strategy setting."
            )
            update_query = client.update_saved_query(
                query_id=existing_searches[query_name], body=json.dumps(query)
            )
            if permissions == Permissions.public:
                client.set_query_permissions(update_query.data.id, {"public": True})

        else:
            logger.warning(
                f"Saved search '{query_name}' already exists in BloodHound. Skipping as per strategy setting."
            )


@dlt.destination(name="bloodhound", skip_dlt_columns_and_tables=True, batch_size=5)
def privilege_zones(
    items: TDataItems,
    table: TTableSchema,
    url: str = dlt.secrets.value,
    token: str = dlt.secrets.value,
    strategy: Strategy = Strategy.skip,
):
    list_items = list(items)
    asset_group_tags = {}
    client = BloodHoundJWT(token=token, base_uri=url)
    unique_zones = {pz_rule["zone"] for pz_rule in list_items}
    for asset_group_tag in client.asset_group_tags.data.tags:
        if asset_group_tag.name in unique_zones:
            asset_group_tags[asset_group_tag.name] = {
                "id": asset_group_tag.id,
                "selectors": {
                    selector.name: selector.id
                    for selector in client.selectors(asset_group_tag.id).data.selectors
                },
            }

    logger.info(f"Processing {len(items)} privilege zones")
    for pz_rule in list_items:
        zone_name = pz_rule["zone"]
        pz_name = pz_rule["name"]
        pz_content = {
            "name": pz_name,
            "description": pz_rule["description"],
            "seeds": [
                {
                    "type": 2,
                    "value": pz_rule["cypher"],
                }
            ],
        }
        asset_group = asset_group_tags.get(zone_name)
        if asset_group:
            asset_group_id = asset_group["id"]
            pz_json_content = json.dumps(pz_content)
            if pz_name not in asset_group["selectors"]:
                logger.info(
                    f"PZ selector '{pz_name}' does not exist. Creating selector for asset group '{zone_name}'"
                )
                client.create_selector(zone_id=asset_group_id, body=pz_json_content)
            elif strategy == Strategy.overwrite:
                logger.warning(
                    f"PZ selector '{pz_name}' already exists in asset group '{zone_name}'. Overwriting as per strategy setting."
                )

                selector_id = asset_group["selectors"][pz_name]
                client.update_selector(
                    zone_id=asset_group_id,
                    selector_id=selector_id,
                    body=pz_json_content,
                )
            else:
                logger.warning(
                    f"PZ selector '{pz_name}' already exists in asset group '{zone_name}'. Skipping as per strategy setting."
                )

        else:
            logger.warning(
                f"PZ selector '{pz_name}' asset group '{zone_name}' not found"
            )
