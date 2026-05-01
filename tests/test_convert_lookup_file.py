import os

os.environ["RUNTIME__LOG_PATH"] = "/tmp/openhound-test-logs"

from openhound.core.app import DEFAULT_LOOKUP_FILE, OpenHound
from openhound.core.convert import Converter, Method
from openhound.core.progress import Progress


def test_convert_uses_default_lookup_file(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    def fake_connect(path: str, read_only: bool = False):
        captured["lookup_file"] = path
        captured["read_only"] = read_only
        return object()

    def fake_run(self, source_object, graph_resources, extra_context, **kwargs):
        captured["lookup_session"] = self.lookup
        return "ok"

    monkeypatch.setattr("openhound.core.app.duckdb.connect", fake_connect)
    monkeypatch.setattr(Converter, "run", fake_run)

    app = OpenHound("test", "test")

    @app.convert(lookup=lambda client: "lookup-session")
    def convert(ctx):
        return object(), {}

    result = app.converter(  # type: ignore[misc]
        input_path=tmp_path,
        output_path=tmp_path,
        progress=Progress.log,
        method=Method.write,
    )

    assert result == "ok"
    assert captured["lookup_file"] == str(DEFAULT_LOOKUP_FILE)
    assert captured["read_only"] is True
    assert captured["lookup_session"] == "lookup-session"


def test_convert_stores_lookup_factory():
    app = OpenHound("test", "test")

    def lookup(client):
        return client

    @app.convert(lookup=lookup)
    def convert(ctx):
        return object(), {}

    assert app.lookup_factory is lookup


def test_convert_accepts_custom_lookup_file(monkeypatch, tmp_path):
    captured: dict[str, object] = {}
    custom_lookup = tmp_path / "custom.duckdb"

    def fake_connect(path: str, read_only: bool = False):
        captured["lookup_file"] = path
        captured["read_only"] = read_only
        return object()

    def fake_run(self, source_object, graph_resources, extra_context, **kwargs):
        captured["lookup_session"] = self.lookup
        return "ok"

    monkeypatch.setattr("openhound.core.app.duckdb.connect", fake_connect)
    monkeypatch.setattr(Converter, "run", fake_run)

    app = OpenHound("test", "test")

    @app.convert(lookup=lambda client: "lookup-session")
    def convert(ctx):
        return object(), {}

    result = app.converter(  # type: ignore[misc]
        input_path=tmp_path,
        output_path=tmp_path,
        lookup_file=custom_lookup,
        progress=Progress.log,
        method=Method.write,
    )

    assert result == "ok"
    assert captured["lookup_file"] == str(custom_lookup)
    assert captured["read_only"] is True
    assert captured["lookup_session"] == "lookup-session"
