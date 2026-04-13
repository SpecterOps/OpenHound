import os
from pathlib import Path

os.environ["RUNTIME__LOG_PATH"] = "/tmp/openhound-test-logs"

from openhound.core.app import DEFAULT_LOOKUP_FILE, OpenHound
from openhound.core.preproc import PreProcessor
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
