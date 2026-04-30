import logging
import os
from pathlib import Path

import duckdb

os.environ["RUNTIME__LOG_PATH"] = "/tmp/openhound-test-logs"

from openhound.core.app import DEFAULT_LOOKUP_FILE, OpenHound
from openhound.core.preproc import PreProcessor, run_transform
from openhound.core.progress import Progress


def test_preproc_uses_default_lookup_file(monkeypatch, tmp_path):
    captured: dict[str, Path] = {}

    def fake_run(self, resources, filters=None):
        captured["output_file"] = self.output_file
        captured["resources"] = resources
        return "ok"

    monkeypatch.setattr(PreProcessor, "run", fake_run)

    app = OpenHound("test", "test")

    @app.preproc()
    def preprocess(ctx):
        return {"resource": "resource"}

    result = app.preprocessor(  # type: ignore[misc]
        input_path=tmp_path,
        progress=Progress.log,
    )

    assert result == "ok"
    assert captured["output_file"] == DEFAULT_LOOKUP_FILE
    assert captured["resources"] == {"resource": "resource"}


def test_preproc_logs_duckdb_transform_errors(monkeypatch, tmp_path, caplog):
    def fake_run(self, source, **kwargs):
        return "ok"

    def missing_table_transform(con: duckdb.DuckDBPyConnection):
        con.execute("SELECT * FROM missing_table")

    monkeypatch.setattr(PreProcessor, "_run", fake_run)
    caplog.set_level(logging.ERROR, logger="openhound.core.preproc")

    preprocessor = PreProcessor(
        name="test",
        input_path=tmp_path,
        output_file=tmp_path / "lookup.duckdb",
        transformer=missing_table_transform,
    )

    result = preprocessor.run(resources={"resource": "resource"})

    assert result == "ok"
    assert any(
        "DuckDB preprocessing failed due to missing table:" in record.message
        and "missing_table" in record.message
        for record in caplog.records
    )


def test_run_transform_logs_transform_name_and_continues(caplog):
    called: list[str] = []

    def missing_table_transform(con: duckdb.DuckDBPyConnection):
        called.append("missing")
        con.execute("SELECT * FROM missing_table")

    def successful_transform(con: duckdb.DuckDBPyConnection):
        called.append("successful")
        con.execute("SELECT 1")

    con = duckdb.connect(":memory:")
    caplog.set_level(logging.ERROR, logger="openhound.core.preproc")

    try:
        run_transform(missing_table_transform, con)
        run_transform(successful_transform, con)
    finally:
        con.close()

    assert called == ["missing", "successful"]
    assert any(
        "DuckDB preprocessing transform 'missing_table_transform' failed due to missing table:"
        in record.message
        and "missing_table" in record.message
        for record in caplog.records
    )
