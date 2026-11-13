import json
import logging
import os
import traceback
from contextlib import nullcontext
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import dlt
import pytest
from dlt.common.configuration import configspec
from dlt.common.configuration.container import Container
from dlt.common.configuration.providers.toml import ConfigTomlProvider
from dlt.common.configuration.specs import BaseConfiguration, CredentialsConfiguration
from dlt.common.configuration.specs.pluggable_run_context import PluggableRunContext
from dlt.common.configuration.utils import TraceLogContext

from openhound.scheduler import service
from openhound.scheduler.configuration import (
    ManagedConfigurationError,
    job_environment,
    managed_job_configuration,
)
from openhound.scheduler.credentials import managed_collection_configuration

DATA = Path(__file__).parent / "test_data"


# Synthetic extension settings exercise DLT resolution without depending on an
# extension's schema, validation rules or collection behavior.
@configspec
class SelectionRule(BaseConfiguration):
    include: bool = True
    match_resource: str | None = None
    match_record: str | None = None


@configspec
class SelectionConfiguration(BaseConfiguration):
    include: bool = False
    limit: int = 10
    rule: list[SelectionRule] | None = None


@configspec
class ExampleCredentials(CredentialsConfiguration):
    secret_value: str | None = None
    workspace: str | None = "default-workspace"
    endpoint_url: str | None = None


@dlt.source(name="example", section="source")
def configuration_source(
    credentials: ExampleCredentials = dlt.secrets.value,
    selection: SelectionConfiguration = dlt.config.value,
):
    rules = [SelectionRule.from_init_value(rule) for rule in selection.rule or []]
    return dlt.resource(
        [
            {
                "secret_value": credentials.secret_value,
                "workspace": credentials.workspace,
                "endpoint": credentials.endpoint_url,
                "include": selection.include,
                "limit": selection.limit,
                "resource_rules": [
                    (rule.match_resource, rule.include)
                    for rule in rules
                    if rule.match_resource
                ],
                "record_rules": [
                    (rule.match_record, rule.include)
                    for rule in rules
                    if rule.match_record
                ],
            }
        ],
        name="resolved_settings",
    )


@pytest.fixture
def params():
    """BHE job params contain configuration only."""
    return json.loads((DATA / "managed_configuration.json").read_text())


@pytest.fixture
def secrets():
    """Simulate a retrieved secret document for the generic document handoff."""
    return json.loads((DATA / "managed_secrets.json").read_text())


def test_job_environment_applies_and_restores_overrides(monkeypatch):
    monkeypatch.setenv("COLLECTION_TOKEN", "scheduler-token")
    monkeypatch.setenv("INHERITED_TOKEN", "scheduler-inherited-token")
    monkeypatch.delenv("JOB_ONLY_SETTING", raising=False)
    scheduler_environment = dict(os.environ)
    worker_environment = {
        "COLLECTION_TOKEN": "job-token",
        "INHERITED_TOKEN": None,
        "JOB_ONLY_SETTING": "job-setting",
    }

    with job_environment(worker_environment):
        assert os.environ["COLLECTION_TOKEN"] == "job-token"
        assert "INHERITED_TOKEN" not in os.environ
        assert os.environ["JOB_ONLY_SETTING"] == "job-setting"

    assert dict(os.environ) == scheduler_environment


def test_job_environment_restores_scheduler_token_after_worker_failure(monkeypatch):
    monkeypatch.setenv("COLLECTION_TOKEN", "scheduler-token")

    with (
        pytest.raises(RuntimeError, match="collection failed"),
        job_environment({"COLLECTION_TOKEN": "job-token"}),
    ):
        raise RuntimeError("collection failed")

    assert os.environ["COLLECTION_TOKEN"] == "scheduler-token"


@pytest.mark.parametrize("include,limit", [(True, 50), (False, 0)])
def test_dlt_source_resolves_nested_settings_and_rule_arrays(params, include, limit):
    selection = params["sources"]["source"]["example"]["selection"]
    selection.update(include=include, limit=limit)
    with managed_job_configuration(params, {}):
        (row,) = list(configuration_source())

    assert row == {
        "secret_value": None,
        "workspace": "job-workspace",
        "endpoint": "https://collector.example.test",
        "include": include,
        "limit": limit,
        "resource_rules": [(".*", True)],
        "record_rules": [("private/.*", False)],
    }


def test_dlt_resolves_retrieved_secrets_per_setting_and_preserves_siblings(
    params, secrets, monkeypatch, caplog
):
    monkeypatch.setenv(
        "SOURCES__SOURCE__EXAMPLE__CREDENTIALS__SECRET_VALUE", "local-secret"
    )
    # The BHE URL and bootstrap secret reference belong to deployment config;
    # the job documents supply only collector settings and collector secrets.
    monkeypatch.setenv(
        "DESTINATION__BLOODHOUNDENTERPRISE__URL", "https://bhe.example.test"
    )
    monkeypatch.setenv("OPENHOUND__AWS_SECRETS_MANAGER__SECRET_NAME", "production/bhe")
    original_params, original_secrets = deepcopy(params), deepcopy(secrets)
    providers = Container()[PluggableRunContext].providers
    previous = providers.providers
    previous_traces = Container()[TraceLogContext]
    with caplog.at_level(logging.DEBUG), managed_job_configuration(params, secrets):
        (row,) = list(configuration_source())
        assert row["secret_value"] == "retrieved-secret-value"
        assert row["workspace"] == "job-workspace"
        assert row["endpoint"] == "https://collector.example.test"
        assert row["include"] is True
        assert row["limit"] == 50
        assert (
            dlt.config.get("destination.bloodhoundenterprise.url", str)
            == "https://bhe.example.test"
        )
        assert (
            dlt.config.get("openhound.aws_secrets_manager.secret_name", str)
            == "production/bhe"
        )
        assert Container()[TraceLogContext].all_traces == []

    assert providers.providers is previous
    assert Container()[TraceLogContext] is previous_traces
    assert params == original_params
    assert secrets == original_secrets
    assert "retrieved-secret-value" not in caplog.text


def test_dynamic_nested_mappings_lists_and_falsy_values_resolve(params):
    options = {
        "new_nested_section": {"enabled": False, "limit": 0},
        "labels": ["one", "two"],
        "empty_list": [],
        "empty_mapping": {},
    }
    params["sources"]["source"]["example"]["future_options"] = options
    overrides = {
        "sources": {
            "source": {
                "example": {"future_options": {"new_nested_section": {"enabled": True}}}
            }
        }
    }
    with managed_job_configuration(params, overrides):
        resolved = dlt.config.get("sources.source.example.future_options", dict)
        assert resolved == {
            **options,
            "new_nested_section": {"enabled": True, "limit": 0},
        }
        assert dlt.config.get("sources.source.example.future_options.labels", list) == [
            "one",
            "two",
        ]
        assert (
            dlt.config.get("sources.source.example.future_options.empty_list", list)
            == []
        )


def test_retrieved_false_zero_and_rule_list_replace_matching_settings(params, secrets):
    secrets["sources"]["source"]["example"]["selection"] = {
        "include": False,
        "limit": 0,
        "rule": [{"include": True, "match_record": "public/.*"}],
    }
    with managed_job_configuration(params, secrets):
        (row,) = list(configuration_source())
    assert row["include"] is False
    assert row["limit"] == 0
    assert row["resource_rules"] == []
    assert row["record_rules"] == [("public/.*", True)]
    assert row["endpoint"] == "https://collector.example.test"


@pytest.mark.parametrize("failed", [False, True], ids=["successful-job", "failed-job"])
def test_successive_jobs_do_not_inherit_configuration_or_secrets(
    params, secrets, failed
):
    original_environment = dict(os.environ)
    try:
        with managed_job_configuration(params, secrets):
            assert (
                next(iter(configuration_source()))["secret_value"]
                == "retrieved-secret-value"
            )
            if failed:
                raise RuntimeError("collection failed")
    except RuntimeError:
        assert failed

    second_job = {
        "sources": {"source": {"example": {"credentials": {"workspace": "second-job"}}}}
    }
    with managed_job_configuration(second_job, {}):
        (row,) = list(configuration_source())
        assert row["secret_value"] is None
        assert row["workspace"] == "second-job"
        assert row["endpoint"] is None
        assert row["include"] is False
        assert row["limit"] == 10
        assert row["resource_rules"] == row["record_rules"] == []
    assert dict(os.environ) == original_environment


@pytest.mark.parametrize(
    "configure", [managed_job_configuration, managed_collection_configuration]
)
@pytest.mark.parametrize("failed", [False, True], ids=["successful-job", "failed-job"])
def test_dlt_runtime_writes_are_job_scoped(
    params, secrets, configure, failed, monkeypatch, tmp_path
):
    providers = Container()[PluggableRunContext].providers
    deployment = ConfigTomlProvider(str(tmp_path))
    deployment.set_value("workers", 8, None, "load")
    deployment_config = deployment.to_toml()
    monkeypatch.setattr(providers, "providers", [deployment])
    previous = providers.providers
    original_params, original_secrets = deepcopy(params), deepcopy(secrets)
    error_type = (
        ManagedConfigurationError
        if configure is managed_collection_configuration
        else RuntimeError
    )
    configured = False

    with (
        pytest.raises(error_type) if failed else nullcontext(),
        configure(params, secrets),
    ):
        assert dlt.config.get("load.workers", int) == 8
        # Extensions can tune DLT during preprocessing/conversion.
        dlt.config["load.workers"] = 1
        dlt.config["data_writer.buffer_max_items"] = 5000
        assert dlt.config.get("load.workers", int) == 1
        assert dlt.config.get("data_writer.buffer_max_items", int) == 5000
        assert (
            next(iter(configuration_source()))["secret_value"]
            == "retrieved-secret-value"
        )
        configured = True
        if failed:
            raise RuntimeError("collection failed")

    assert configured
    assert providers.providers is previous
    assert deployment.to_toml() == deployment_config
    assert params == original_params
    assert secrets == original_secrets
    with configure(params, secrets):
        assert dlt.config.get("load.workers", int) == 8
        assert dlt.config.get("data_writer.buffer_max_items", int) is None


@pytest.mark.parametrize(
    "configure", [managed_job_configuration, managed_collection_configuration]
)
def test_runtime_writes_preserve_managed_setting_precedence(configure):
    params = {"load": {"workers": 4}}
    secrets = {"load": {"workers": 2}}
    with configure(params, secrets):
        dlt.config["load.workers"] = 1
        assert dlt.config.get("load.workers", int) == 2
    assert params == {"load": {"workers": 4}}
    assert secrets == {"load": {"workers": 2}}


@pytest.mark.parametrize(
    "document",
    [
        {"limit": 50},
        {"sources.source.example.selection.include": True},
        {"SOURCES__SOURCE__EXAMPLE__SELECTION__INCLUDE": True},
        {"labels": ["private-credential"]},
        {"sources": {}, "private-key": "private-credential"},
    ],
)
@pytest.mark.parametrize("secret_document", [False, True])
def test_flat_configuration_is_rejected_without_exposing_keys_or_values(
    params, document, secret_document
):
    with (
        pytest.raises(
            ManagedConfigurationError, match="flat configuration is not supported"
        ) as error,
        managed_job_configuration(
            params if secret_document else document, document if secret_document else {}
        ),
    ):
        pytest.fail("flat configuration must not reach the pipeline")
    assert "private-credential" not in str(error.value)
    assert "private-key" not in str(error.value)


def test_empty_params_are_rejected_even_with_retrieved_secrets(secrets):
    with (
        pytest.raises(ManagedConfigurationError, match="non-empty nested JSON object"),
        managed_job_configuration({}, secrets),
    ):
        pytest.fail("empty params must not reach the pipeline")


def test_custom_sections_and_values_are_passed_to_dlt_without_schema_validation():
    settings = {
        "future_options": {"optional_value": None, "labels": [], "limit": 0},
        "enabled": False,
    }
    with managed_job_configuration({"custom_pipeline": settings}, {}):
        assert dlt.config.get("custom_pipeline", dict) == settings


def test_unused_nested_settings_are_ignored_during_dlt_source_resolution(params):
    with managed_job_configuration(params, {}):
        expected = list(configuration_source())

    params["unrelated_pipeline"] = {"extra_metadata": ["ignored"]}
    source = params["sources"]["source"]["example"]
    source["unused_source_setting"] = {"arbitrary": None}
    source["credentials"]["unused_credential_setting"] = [False, 0]
    source["selection"]["unused_selection_setting"] = "not a collector option"
    for rule in source["selection"]["rule"]:
        rule["unused_rule_setting"] = {"arbitrary": None}

    with managed_job_configuration(params, {}):
        assert list(configuration_source()) == expected


def test_dlt_type_errors_are_sanitized_and_provider_is_removed(params, caplog):
    providers = Container()[PluggableRunContext].providers
    previous = providers.providers
    params["sources"]["source"]["example"]["selection"]["limit"] = "private-credential"
    with (
        caplog.at_level(logging.DEBUG),
        pytest.raises(ManagedConfigurationError) as error,
        managed_job_configuration(params, {}),
    ):
        list(configuration_source())
    assert "could not be resolved" in str(error.value)
    assert "private-credential" not in "".join(traceback.format_exception(error.value))
    assert "private-credential" not in caplog.text
    assert providers.providers is previous


def test_worker_applies_configuration_before_collector_loading(
    params, secrets, monkeypatch
):
    observed = []

    def load_collectors():
        observed.append(next(iter(configuration_source())))
        return SimpleNamespace(
            collectors=[
                SimpleNamespace(
                    name="example",
                    dlt_source=configuration_source,
                    package_version="test",
                    metadata=None,
                )
            ]
        )

    monkeypatch.setattr(service.CollectorManager, "from_entrypoint", load_collectors)
    monkeypatch.setattr(
        service.dataflow,
        "pipeline",
        lambda extension: {"settings": list(extension.dlt_source())},
    )
    result = service._subprocess_collect("example", "job-1", params, secrets)
    assert result.results["settings"] == observed
    assert observed[0]["secret_value"] == "retrieved-secret-value"
