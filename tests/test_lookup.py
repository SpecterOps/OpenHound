import logging

import duckdb

from openhound.core.lookup import LookupManager


def test_find_single_object_returns_none_on_duckdb_error(caplog):
    client = duckdb.connect(":memory:")
    lookup = LookupManager(client, "main")
    caplog.set_level(logging.ERROR, logger="openhound.core.lookup")

    try:
        result = lookup._find_single_object("SELECT id FROM missing_table")
    finally:
        client.close()

    assert result is None
    assert any(
        "DuckDB lookup failed, missing table:" in record.message
        and "missing_table" in record.message
        for record in caplog.records
    )


def test_find_all_objects_returns_empty_list_on_duckdb_error(caplog):
    client = duckdb.connect(":memory:")
    lookup = LookupManager(client, "main")
    caplog.set_level(logging.ERROR, logger="openhound.core.lookup")

    try:
        result = lookup._find_all_objects("SELECT id FROM missing_table")
    finally:
        client.close()

    assert result == []
    assert any(
        "DuckDB lookup failed, missing table:" in record.message
        and "missing_table" in record.message
        for record in caplog.records
    )
