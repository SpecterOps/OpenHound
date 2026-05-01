import os

os.environ["RUNTIME__LOG_PATH"] = "/tmp/openhound-test-logs"

import pytest

from openhound.core.progress import Progress


def test_notebook_pipeline_faker(monkeypatch, tmp_path):
    """Run the openhound_faker collector and test that the pipeline notebook is succesfully loaded with a preview/sample data"""
    pytest.importorskip("marimo")
    faker_main = pytest.importorskip("openhound_faker.main")

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    faker_main.app.collector(
        output_path=tmp_path / "output",
        resources=[],
        progress=Progress.log,
    )

    from openhound.notebooks.pipeline import app

    _outputs, defs = app.run()

    assert defs["selected_pipeline"].value == "faker_collect"
    assert defs["matched_extension_name"] == "faker"
    assert defs["selected_table"].value == "fake_computer"
    assert defs["sample_df"].height > 0
    assert defs["as_node_df"].height > 0
    assert "prop_hostname" in defs["as_node_df"].columns
