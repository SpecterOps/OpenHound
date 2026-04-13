import os

os.environ["RUNTIME__LOG_PATH"] = "/tmp/openhound-test-logs"

from openhound.core.collect import Collector
from openhound.core.progress import Progress


def test_collector_creates_output_directory(monkeypatch, tmp_path):
    output_path = tmp_path / "new-output-dir"
    captured = {}

    def fake_run(self, source, **kwargs):
        captured["exists"] = output_path.is_dir()
        return "ok"

    monkeypatch.setattr("openhound.core.collect.logger_override.set_handler", lambda name: None)
    monkeypatch.setattr(Collector, "_run", fake_run)

    collector = Collector(name="test", output_path=output_path, progress=Progress.log)
    result = collector.run(object())

    assert result == "ok"
    assert captured["exists"] is True
