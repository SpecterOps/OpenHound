import os
from types import SimpleNamespace

os.environ["RUNTIME__LOG_PATH"] = "/tmp/openhound-test-logs"

from openhound.core.convert import Converter, Method
from openhound.core.progress import Progress


def test_converter_creates_output_directory_for_write(monkeypatch, tmp_path):
    output_path = tmp_path / "new-opengraph-dir"
    captured = {}

    def fake_run(self, source, **kwargs):
        captured["exists"] = output_path.is_dir()
        return "ok"

    monkeypatch.setattr("openhound.core.convert.logger_override.set_handler", lambda name: None)
    monkeypatch.setattr(Converter, "_run", fake_run)

    converter = Converter(
        name="test",
        input_path=tmp_path,
        lookup=None,
        output_path=output_path,
        source_kind="test",
        progress=Progress.log,
        method=Method.write,
    )
    source_object = SimpleNamespace(resources={})
    result = converter.run(source_object, graph_resources=[], extra_context={})

    assert result == "ok"
    assert captured["exists"] is True
