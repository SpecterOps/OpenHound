import os

import dlt
import pytest
import typer
from dlt.common.configuration.container import Container
from dlt.common.configuration.providers.toml import StringTomlProvider
from dlt.common.configuration.specs import PluggableRunContext
from dlt.extract.exceptions import ResourcesNotFoundError
from typer.testing import CliRunner

os.environ["RUNTIME__LOG_PATH"] = "/tmp/openhound-test-logs"

from openhound_sample.main import app as sample_app

from openhound.core.collect import Collector
from openhound.core.progress import Progress
from openhound.scheduler import dataflow

SAMPLE_RESOURCES = ["computer", "installed_software", "sample_environment", "user"]


@pytest.fixture
def collector_app(monkeypatch):
    providers = Container()[PluggableRunContext].providers
    monkeypatch.setattr(providers, "providers", [])
    monkeypatch.setattr("openhound.core.collect.logger_override.set_handler", lambda name: None)

    def fake_run(self, source, **kwargs):
        return sorted(source.selected_resources)

    monkeypatch.setattr(Collector, "_run", fake_run)
    return sample_app


@pytest.mark.parametrize(
    "config, expected",
    [
        ("", SAMPLE_RESOURCES),
        ('[sources.source.sample]\nresources = []', SAMPLE_RESOURCES),
        ('[sources.source.sample]\nresources = ["user"]', ["user"]),
        ('[sources.sample]\nresources = ["computer"]', ["computer"])
    ],
)
def test_collect_uses_source_resource_config(collector_app, tmp_path, config, expected):
    dlt.config.register_provider(StringTomlProvider(config))

    result = collector_app.collector(
        output_path=tmp_path, resources=[], progress=Progress.log
    )

    assert result == expected


@pytest.mark.parametrize(
    "resources, expected",
    [([], ["computer"]), (["user"], ["user"])],
)
def test_cli_resources_override_config(
        collector_app, monkeypatch, tmp_path, resources, expected
):
    dlt.config.register_provider(
        StringTomlProvider('[sources.sample]\nresources = ["computer"]')
    )
    captured = []

    def fake_run(self, source, **kwargs):
        captured.extend(sorted(source.selected_resources))

    cli = typer.Typer()
    cli.command()(collector_app.collector)
    monkeypatch.setattr(Collector, "_run", fake_run)
    result = CliRunner().invoke(cli, [str(tmp_path), *resources])

    assert result.exit_code == 0, result.output
    assert captured == expected


@pytest.mark.parametrize(
    "value", ['123', '"user"', '{ user = true }', '[123]', '[""]', '[" "]']
)
def test_collect_rejects_invalid_resource_config(collector_app, tmp_path, value):
    dlt.config.register_provider(
        StringTomlProvider(f"[sources.sample]\nresources = {value}")
    )

    with pytest.raises(ValueError, match="resources"):
        collector_app.collector(
            output_path=tmp_path, resources=[], progress=Progress.log
        )


def test_collect_rejects_unknown_resource(collector_app, tmp_path):
    dlt.config.register_provider(
        StringTomlProvider('[sources.sample]\nresources = ["unknown"]')
    )

    with pytest.raises(ResourcesNotFoundError):
        collector_app.collector(
            output_path=tmp_path, resources=[], progress=Progress.log
        )


def test_scheduled_collect_uses_resource_config(collector_app, monkeypatch, tmp_path):
    dlt.config.register_provider(
        StringTomlProvider('[sources.sample]\nresources = ["user"]')
    )
    monkeypatch.setattr(dataflow, "Path", lambda path: tmp_path)
    monkeypatch.setattr(dataflow, "extracted_resources", lambda load_info: load_info)

    assert dataflow.collect(collector_app) == ["user"]
