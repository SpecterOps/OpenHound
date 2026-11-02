import json
import logging
import os
import traceback
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import dlt
import pytest
from dlt.common.configuration import configspec
from dlt.common.configuration.container import Container
from dlt.common.configuration.specs import BaseConfiguration, CredentialsConfiguration
from dlt.common.configuration.specs.pluggable_run_context import PluggableRunContext
from dlt.common.configuration.utils import TraceLogContext

from openhound.scheduler import service
from openhound.scheduler.configuration import (
    ManagedConfigurationError,
    managed_job_configuration,
)

DATA = Path(__file__).parent / "test_data"


# The relevant openhound-aws model shapes. Exercise the actual DLT source
# resolver without requiring the AWS extension's native build or AWS requests.
@configspec
class BucketPolicyRule(BaseConfiguration):
    include: bool = True
    match_bucket: str | None = None
    match_object: str | None = None


@configspec
class BucketPolicy(BaseConfiguration):
    include: bool = False
    max_objects: int = 10
    rule: list[BucketPolicyRule] | None = None


@configspec
class AWSProfiles(CredentialsConfiguration):
    profile_names: list[str] = None
    region_name: str | None = "us-east-1"
    endpoint_url: str | None = None


@dlt.source(name="aws", section="source")
def aws_configuration_source(
    credentials: AWSProfiles = dlt.secrets.value,
    bucket_policy: BucketPolicy = dlt.config.value,
):
    rules = [
        BucketPolicyRule.from_init_value(rule) for rule in bucket_policy.rule or []
    ]
    return dlt.resource(
        [
            {
                "profiles": credentials.profile_names,
                "region": credentials.region_name,
                "endpoint": credentials.endpoint_url,
                "include": bucket_policy.include,
                "max_objects": bucket_policy.max_objects,
                "bucket_rules": [
                    (rule.match_bucket, rule.include)
                    for rule in rules
                    if rule.match_bucket
                ],
                "object_rules": [
                    (rule.match_object, rule.include)
                    for rule in rules
                    if rule.match_object
                ],
            }
        ],
        name="resolved_settings",
    )


@pytest.fixture
def params():
    return json.loads((DATA / "managed_aws_configuration.json").read_text())


@pytest.fixture
def secrets():
    return json.loads((DATA / "managed_aws_secrets.json").read_text())


@pytest.mark.parametrize("include,max_objects", [(True, 50), (False, 0)])
def test_dlt_source_resolves_nested_aws_settings_and_rule_arrays(
    params, include, max_objects
):
    bucket = params["sources"]["source"]["aws"]["bucket_policy"]
    bucket.update(include=include, max_objects=max_objects)
    with managed_job_configuration(params, {}):
        (row,) = list(aws_configuration_source())

    assert row == {
        "profiles": ["bhe-profile"],
        "region": "us-west-2",
        "endpoint": "https://aws.example.test",
        "include": include,
        "max_objects": max_objects,
        "bucket_rules": [(".*", True)],
        "object_rules": [("private/.*", False)],
    }


def test_dlt_resolves_retrieved_secrets_per_setting_and_preserves_siblings(
    params, secrets, monkeypatch, caplog
):
    monkeypatch.setenv("SOURCES__SOURCE__AWS__CREDENTIALS__PROFILE_NAMES", '["local"]')
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
        (row,) = list(aws_configuration_source())
        assert row["profiles"] == ["retrieved-profile-a", "retrieved-profile-b"]
        assert row["region"] == "us-west-2"
        assert row["endpoint"] == "https://aws.example.test"
        assert row["include"] is True
        assert row["max_objects"] == 50
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
    for credential in ("retrieved-profile-a", "retrieved-profile-b"):
        assert credential not in caplog.text


def test_dynamic_nested_mappings_lists_and_falsy_values_resolve(params):
    options = {
        "new_nested_section": {"enabled": False, "limit": 0},
        "labels": ["one", "two"],
        "empty_list": [],
        "empty_mapping": {},
    }
    params["sources"]["source"]["aws"]["future_options"] = options
    overrides = {
        "sources": {
            "source": {
                "aws": {"future_options": {"new_nested_section": {"enabled": True}}}
            }
        }
    }
    with managed_job_configuration(params, overrides):
        resolved = dlt.config.get("sources.source.aws.future_options", dict)
        assert resolved == {
            **options,
            "new_nested_section": {"enabled": True, "limit": 0},
        }
        assert dlt.config.get("sources.source.aws.future_options.labels", list) == [
            "one",
            "two",
        ]
        assert (
            dlt.config.get("sources.source.aws.future_options.empty_list", list) == []
        )


def test_retrieved_false_zero_and_rule_list_replace_matching_settings(params, secrets):
    secrets["sources"]["source"]["aws"]["bucket_policy"] = {
        "include": False,
        "max_objects": 0,
        "rule": [{"include": True, "match_object": "public/.*"}],
    }
    with managed_job_configuration(params, secrets):
        (row,) = list(aws_configuration_source())
    assert row["include"] is False
    assert row["max_objects"] == 0
    assert row["bucket_rules"] == []
    assert row["object_rules"] == [("public/.*", True)]
    assert row["endpoint"] == "https://aws.example.test"


@pytest.mark.parametrize("failed", [False, True], ids=["successful-job", "failed-job"])
def test_successive_jobs_do_not_inherit_configuration_or_secrets(
    params, secrets, failed
):
    original_environment = dict(os.environ)
    try:
        with managed_job_configuration(params, secrets):
            assert next(iter(aws_configuration_source()))["profiles"] == [
                "retrieved-profile-a",
                "retrieved-profile-b",
            ]
            if failed:
                raise RuntimeError("collection failed")
    except RuntimeError:
        assert failed

    second_job = {
        "sources": {
            "source": {"aws": {"credentials": {"profile_names": ["second-job"]}}}
        }
    }
    with managed_job_configuration(second_job, {}):
        (row,) = list(aws_configuration_source())
        assert row["profiles"] == ["second-job"]
        assert row["region"] == "us-east-1"
        assert row["endpoint"] is None
        assert row["include"] is False
        assert row["max_objects"] == 10
        assert row["bucket_rules"] == row["object_rules"] == []
    assert dict(os.environ) == original_environment


@pytest.mark.parametrize(
    "document",
    [
        {"max_objects": 50},
        {"sources.source.aws.bucket_policy.include": True},
        {"SOURCES__SOURCE__AWS__BUCKET_POLICY__INCLUDE": True},
        {"profile_names": ["private-credential"]},
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
        expected = list(aws_configuration_source())

    params["unrelated_pipeline"] = {"extra_metadata": ["ignored"]}
    aws = params["sources"]["source"]["aws"]
    aws["unused_source_setting"] = {"arbitrary": None}
    aws["credentials"]["unused_credential_setting"] = [False, 0]
    aws["bucket_policy"]["unused_bucket_setting"] = "not a collector option"
    for rule in aws["bucket_policy"]["rule"]:
        rule["unused_rule_setting"] = {"arbitrary": None}

    with managed_job_configuration(params, {}):
        assert list(aws_configuration_source()) == expected


def test_dlt_type_errors_are_sanitized_and_provider_is_removed(params, caplog):
    providers = Container()[PluggableRunContext].providers
    previous = providers.providers
    params["sources"]["source"]["aws"]["bucket_policy"][
        "max_objects"
    ] = "private-credential"
    with (
        caplog.at_level(logging.DEBUG),
        pytest.raises(ManagedConfigurationError) as error,
        managed_job_configuration(params, {}),
    ):
        list(aws_configuration_source())
    assert "could not be resolved" in str(error.value)
    assert "private-credential" not in "".join(traceback.format_exception(error.value))
    assert "private-credential" not in caplog.text
    assert providers.providers is previous


def test_worker_applies_configuration_before_collector_loading(
    params, secrets, monkeypatch
):
    observed = []

    def load_collectors():
        observed.append(next(iter(aws_configuration_source())))
        return SimpleNamespace(
            collectors=[
                SimpleNamespace(
                    name="aws",
                    dlt_source=aws_configuration_source,
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
    result = service._subprocess_collect("aws", "job-1", params, secrets)
    assert result.results["settings"] == observed
    assert observed[0]["profiles"] == ["retrieved-profile-a", "retrieved-profile-b"]
