from enum import Enum
from pathlib import Path

import dlt

from openhound.core.models.privilege_zone import PrivilegeZone, PrivilegeZoneExtended
from openhound.core.models.saved_search import SavedSearch, SavedSearchExtended


class Format(str, Enum):
    yaml = "yaml"
    json = "json"


@dlt.source(name="bloodhound_config", max_table_nesting=0)
def bloodhound_config(
    saved_queries: list[Path] | None = None,
    privilege_zones: list[Path] | None = None,
    file_format: Format = Format.json,
):
    saved_queries = saved_queries if saved_queries else []
    privilege_zones = privilege_zones if privilege_zones else []

    @dlt.resource(name="privilege_zone", columns=PrivilegeZone)
    def privilege_zone():
        for pz in privilege_zones:
            yield (
                PrivilegeZone.from_json(pz)
                if file_format.value == Format.json
                else PrivilegeZoneExtended.from_json(pz)
            )

    @dlt.resource(name="saved_search", columns=SavedSearch)
    def queries():
        for query in saved_queries:
            yield (
                SavedSearch.from_json(query)
                if file_format.value == Format.json
                else SavedSearchExtended.from_yaml(query)
            )

    return (queries, privilege_zone)
