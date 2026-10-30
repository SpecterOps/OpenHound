import logging
import multiprocessing
import os
import stat
import tempfile
import time
import traceback
import zipfile
from concurrent.futures import Future, ProcessPoolExecutor
from configparser import ConfigParser
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import boto3
import dlt
import pytest
from botocore.exceptions import ClientError, ParamValidationError
from botocore.httpsession import URLLib3Session
from botocore.session import Session as BotocoreSession
from botocore.stub import Stubber
from dlt.common.configuration.container import Container
from dlt.common.configuration.providers.doc import CustomLoaderDocProvider
from dlt.common.configuration.providers.provider import ConfigProvider
from dlt.common.configuration.specs.pluggable_run_context import PluggableRunContext
from dlt.common.configuration.utils import TraceLogContext
from dlt.destinations import filesystem

from openhound.core import logging as openhound_logging
from openhound.core.clients.bloodhound_enterprise import ManagedJobOutcome
from openhound.core.resources import safe_defer_wrapper, safe_resource_wrapper
from openhound.core.support_bundle import create_support_bundle
from openhound.scheduler import aws_profile, service
from openhound.scheduler.aws_profile import (
    AWSAccessKeyCredentials,
    AWSProfileWorkspace,
    cleanup_orphaned_aws_profiles,
    managed_aws_profile,
)
from openhound.scheduler.configuration import ManagedConfigurationError

ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"
SECRET_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
PAIR = AWSAccessKeyCredentials(ACCESS_KEY, SECRET_KEY)
SOURCE_ACCOUNT = "999999999999"
MANAGEMENT_ACCOUNT = "111111111111"
ROLE_ACCOUNT = "333333333333"
ROLE_ARN = f"arn:aws:iam::{ROLE_ACCOUNT}:role/security/CollectorRole"
ROLE_ACCESS_KEY = "ASIAIOSFODNN7EXAMPLE"
ROLE_SECRET_KEY = "T" * 40
ROLE_TOKEN = "assumed-session-token"


@pytest.fixture(autouse=True)
def private_workspaces(tmp_path, monkeypatch):
    monkeypatch.setattr(
        aws_profile,
        "tempfile",
        SimpleNamespace(gettempdir=lambda: str(tmp_path), mkdtemp=tempfile.mkdtemp),
    )
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv(
        "AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "scheduler-credentials")
    )
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "scheduler-config"))
    monkeypatch.setattr(
        URLLib3Session,
        "send",
        lambda *args, **kwargs: pytest.fail("Live AWS requests are forbidden"),
    )


@pytest.fixture
def workspace():
    workspace = AWSProfileWorkspace()
    try:
        yield workspace
    finally:
        workspace.cleanup()


@pytest.fixture
def profile_assumptions(monkeypatch):
    """Stub the STS clients botocore creates internally for role profiles."""
    create_client = BotocoreSession.create_client
    pending, stubbers = [], []

    def expect(params, *, denied=False, secret=ROLE_SECRET_KEY):
        pending.append((params, denied, secret))

    def stub_sts(session, name, *args, **kwargs):
        client = create_client(session, name, *args, **kwargs)
        if name == "sts" and kwargs.get("aws_access_key_id") == ACCESS_KEY:
            frozen = client._request_signer._credentials.get_frozen_credentials()
            assert (frozen.access_key, frozen.secret_key, frozen.token) == (
                ACCESS_KEY,
                SECRET_KEY,
                None,
            )
            assert pending, "Unexpected initial role assumption"
            params, denied, secret = pending.pop(0)
            stubber = Stubber(client)
            if denied:
                stubber.add_client_error(
                    "assume_role",
                    service_error_code="AccessDenied",
                    service_message="Role trust denied",
                    expected_params=params,
                )
            else:
                stubber.add_response(
                    "assume_role",
                    {
                        "Credentials": {
                            "AccessKeyId": ROLE_ACCESS_KEY,
                            "SecretAccessKey": secret,
                            "SessionToken": ROLE_TOKEN,
                            "Expiration": datetime.now(UTC) + timedelta(hours=1),
                        }
                    },
                    params,
                )
            stubber.activate()
            stubbers.append(stubber)
        return client

    monkeypatch.setattr(BotocoreSession, "create_client", stub_sts)
    try:
        yield expect
        assert not pending, "Expected initial role assumption did not occur"
        for stubber in stubbers:
            stubber.assert_no_pending_responses()
    finally:
        for stubber in stubbers:
            stubber.deactivate()


def _identity(account=SOURCE_ACCOUNT):
    return {
        "Account": account,
        "Arn": f"arn:aws:iam::{account}:user/collector",
        "UserId": "collector",
    }


def _assert_source_credentials(session):
    credentials = session.get_credentials()
    assert credentials.method == "shared-credentials-file"
    frozen = credentials.get_frozen_credentials()
    assert (frozen.access_key, frozen.secret_key, frozen.token) == (
        ACCESS_KEY,
        SECRET_KEY,
        None,
    )


def _assert_role_credentials(session):
    credentials = session.get_credentials()
    assert credentials.method == "assume-role"
    frozen = credentials.get_frozen_credentials()
    assert (frozen.access_key, frozen.secret_key, frozen.token) == (
        ROLE_ACCESS_KEY,
        ROLE_SECRET_KEY,
        ROLE_TOKEN,
    )


@pytest.mark.parametrize("custom_session", [False, True], ids=["default", "custom"])
def test_role_profile_resolves_through_real_sdk(
    custom_session,
    workspace,
    profile_assumptions,
    monkeypatch,
):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "scheduler-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "scheduler-secret")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "scheduler-token")
    monkeypatch.setenv("AWS_PROFILE", "scheduler-profile")
    monkeypatch.setenv("AWS_ROLE_ARN", "arn:aws:iam::123456789012:role/OtherRole")
    options = {"role_arn": ROLE_ARN, "region_name": "eu-west-1"}
    if custom_session:
        options.update(
            role_session_name="collection-session", external_id="customer/123"
        )
    params = {"sources": {"source": {"aws": {"credentials": options}}}}
    original_params, original_environment = deepcopy(params), dict(os.environ)
    with managed_aws_profile(params, PAIR, workspace.directory) as profile:
        session_name = "collection-session" if custom_session else profile
        expected = {"RoleArn": ROLE_ARN, "RoleSessionName": session_name}
        if custom_session:
            expected["ExternalId"] = "customer/123"
        profile_assumptions(expected)
        settings = dlt.secrets.get("sources.source.aws.credentials", dict)
        assert settings["profile_name"] == profile
        assert settings["region_name"] == "eu-west-1"
        session = boto3.Session(
            profile_name=settings["profile_name"], region_name=settings["region_name"]
        )
        _assert_role_credentials(session)
        sts = session.client("sts")
        with Stubber(sts) as stubber:
            identity = {
                "Account": ROLE_ACCOUNT,
                "Arn": f"arn:aws:sts::{ROLE_ACCOUNT}:assumed-role/CollectorRole/{session_name}",
                "UserId": f"role:{session_name}",
            }
            stubber.add_response("get_caller_identity", identity, {})
            assert sts.get_caller_identity() == identity
        iam = session.client("iam")
        assert iam._request_signer._credentials is session.get_credentials()
        for key in ("role_arn", "role_session_name", "external_id"):
            assert dlt.secrets.get(
                f"sources.source.aws.credentials.{key}", str
            ) == options.get(key)
        config, credentials = ConfigParser(), ConfigParser()
        config.read(workspace.directory / "config")
        credentials.read(workspace.directory / "credentials")
        role = config[f"profile {profile}"]
        assert role["role_arn"] == ROLE_ARN
        assert role["role_session_name"] == session_name
        assert credentials.sections() == [role["source_profile"]]
        assert credentials[role["source_profile"]]["aws_access_key_id"] == ACCESS_KEY
        assert profile not in credentials
        for name in ("config", "credentials"):
            assert stat.S_IMODE((workspace.directory / name).stat().st_mode) == 0o600
        assert Container()[TraceLogContext].all_traces == []
    assert params == original_params
    assert dict(os.environ) == original_environment
    assert not (workspace.directory / "config").exists()
    assert not (workspace.directory / "credentials").exists()


def test_role_profile_refreshes_temporary_credentials(workspace, profile_assumptions):
    params = {"sources": {"source": {"aws": {"credentials": {"role_arn": ROLE_ARN}}}}}
    with managed_aws_profile(params, PAIR, workspace.directory) as profile:
        expected = {"RoleArn": ROLE_ARN, "RoleSessionName": profile}
        profile_assumptions(expected)
        session = boto3.Session(profile_name=profile, region_name="us-east-1")
        _assert_role_credentials(session)
        profile_assumptions(expected, secret="R" * 40)
        provider = session._session.get_component("credential_provider").get_provider(
            "assume-role"
        )
        provider.cache.clear()
        credentials = session.get_credentials()
        credentials._expiry_time = datetime.now(UTC) - timedelta(seconds=1)
        assert credentials.get_frozen_credentials().secret_key == "R" * 40


def test_denied_profile_role_fails_without_source_fallback(
    workspace,
    profile_assumptions,
    caplog,
):
    original_environment = dict(os.environ)
    params = {"sources": {"source": {"aws": {"credentials": {"role_arn": ROLE_ARN}}}}}
    with (
        pytest.raises(ManagedConfigurationError, match="Managed AWS collection failed"),
        managed_aws_profile(params, PAIR, workspace.directory) as profile,
    ):
        profile_assumptions(
            {"RoleArn": ROLE_ARN, "RoleSessionName": profile}, denied=True
        )
        session = boto3.Session(profile_name=profile, region_name="us-east-1")
        session.get_credentials().get_frozen_credentials()
        pytest.fail("Denied role must not fall back to source credentials")
    assert "AssumeRole returned AccessDenied" in caplog.text
    assert ACCESS_KEY not in caplog.text and SECRET_KEY not in caplog.text
    assert dict(os.environ) == original_environment
    assert not (workspace.directory / "config").exists()
    assert not (workspace.directory / "credentials").exists()


@pytest.mark.parametrize(
    "outcome",
    [
        "assume-role-denied",
        "invalid-source-key",
        "success",
        "recoverable",
        "empty",
    ],
)
def test_initial_identity_controls_worker_and_managed_outcome(
    outcome, profile_assumptions, monkeypatch, tmp_path, caplog
):
    """Real SDK profile resolution + DLT + worker; mock only AWS/BHE IO and executor."""
    failed = outcome in {"assume-role-denied", "invalid-source-key"}
    role = outcome != "invalid-source-key"
    options = {
        "region_name": "eu-west-1",
        "endpoint_url": "https://sts.example.test",
    }
    if role:
        options.update(role_arn=ROLE_ARN, role_session_name="initial-collection")
        profile_assumptions(
            {"RoleArn": ROLE_ARN, "RoleSessionName": "initial-collection"},
            denied=outcome == "assume-role-denied",
        )
    params = {"sources": {"source": {"aws": {"credentials": options}}}}
    # Defaults must not become an alternative identity after a role denial.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "scheduler-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "scheduler-secret")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "scheduler-token")
    original_environment = dict(os.environ)
    providers = Container()[PluggableRunContext].providers
    original_providers = providers.providers
    create_client = BotocoreSession.create_client
    identity_requests, stubbers, stages, ends, uploads = [], [], [], [], []

    def stub_identity(session, name, *args, **kwargs):
        client = create_client(session, name, *args, **kwargs)
        if name == "sts" and kwargs.get("aws_access_key_id") is None:
            # Stubber bypasses request signing. Resolve the real SDK credential
            # chain first so the initial AssumeRole still runs (and can fail).
            credentials = client._request_signer._credentials
            frozen = credentials.get_frozen_credentials()
            assert credentials.method == (
                "assume-role" if role else "shared-credentials-file"
            )
            assert frozen.access_key == (ROLE_ACCESS_KEY if role else ACCESS_KEY)
            assert client.meta.region_name == "eu-west-1"
            assert client.meta.endpoint_url == "https://sts.example.test"
            identity_requests.append(frozen.access_key)
            stubber = Stubber(client)
            if outcome == "invalid-source-key":
                stubber.add_client_error(
                    "get_caller_identity",
                    service_error_code="InvalidClientTokenId",
                    expected_params={},
                )
            else:
                stubber.add_response("get_caller_identity", _identity(ROLE_ACCOUNT), {})
            stubber.activate()
            stubbers.append(stubber)
        return client

    monkeypatch.setattr(BotocoreSession, "create_client", stub_identity)

    def denied_resource():
        raise ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "Optional bucket denied"}},
            "GetBucketPolicy",
        )
        yield  # Resource errors occur on iteration, as in the AWS extension.

    def denied_defer():
        raise ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "Optional object denied"}},
            "GetObject",
        )

    def collect(**kwargs):
        stages.append("collect")
        settings = dlt.secrets.get("sources.source.aws.credentials", dict)
        assert settings["profile_name"].startswith("openhound-job-")
        assert (directory / "credentials").exists()
        resources = [dlt.resource([], name="accounts")]
        if outcome != "empty":
            resources = [dlt.resource([{"AccountId": ROLE_ACCOUNT}], name="accounts")]
        if outcome == "recoverable":
            resources.append(
                dlt.resource(safe_resource_wrapper(denied_resource, "bucket_policy"))()
            )

            @dlt.resource
            def objects():
                yield dlt.defer(safe_defer_wrapper(denied_defer))()

            resources.append(objects())
        pipeline = dlt.pipeline(
            pipeline_name="initial_auth_regression",
            pipelines_dir=str(tmp_path / "pipelines"),
            destination=filesystem(bucket_url=str(tmp_path / "collected")),
            dataset_name="aws",
        )
        return pipeline.run(resources)

    def preprocess(extension):
        stages.append("preprocess")
        return []

    def convert(extension, output_path):
        stages.append("convert")
        (output_path / "graph.json").write_text('{"graph": {}}')
        return []

    collector = SimpleNamespace(
        name="aws",
        dlt_source=object(),
        package_version="test",
        metadata=None,
        collector=collect,
        preprocessor=object(),
    )
    monkeypatch.setattr(
        service.CollectorManager,
        "from_entrypoint",
        lambda: SimpleNamespace(collectors=[collector]),
    )
    monkeypatch.setattr(service.dataflow, "preprocess", preprocess)
    monkeypatch.setattr(service.dataflow, "convert", convert)
    output_directory = tmp_path / "staged-graph"

    def create_output(**kwargs):
        output_directory.mkdir()
        return str(output_directory)

    monkeypatch.setattr(service, "tempfile", SimpleNamespace(mkdtemp=create_output))
    scheduler = service.Service(
        "https://bhe.example.test", "token", "id", "aws", managed=True
    )
    monkeypatch.setattr(scheduler.client, "start_collector_job", lambda job_id: None)
    monkeypatch.setattr(
        scheduler.client,
        "end_managed_job",
        lambda *args, **kwargs: ends.append((args, kwargs)),
    )
    monkeypatch.setattr(
        scheduler.client,
        "upload_collector_job_data",
        lambda *args: uploads.append(args),
    )

    def submit(function, *args):
        nonlocal directory
        directory = scheduler._aws_profile_workspace.directory
        future = Future()
        try:
            future.set_result(function(*args))
        except Exception as error:  # noqa: BLE001 - emulate ProcessPoolExecutor
            future.set_exception(error)
        return future

    directory = None
    monkeypatch.setattr(scheduler.executor, "submit", submit)
    try:
        scheduler.run_claimed_job("job-initial-auth", params, PAIR)
        assert not (directory / "credentials").exists()
        assert not (directory / "config").exists()
        assert dict(os.environ) == original_environment
        assert providers.providers is original_providers
        if failed:
            with pytest.raises(
                ManagedConfigurationError, match="Managed AWS collection failed"
            ):
                scheduler.future.result()
            assert stages == []
            assert "completed successfully" not in caplog.text
            assert (
                "AssumeRole returned AccessDenied"
                if role
                else "GetCallerIdentity returned InvalidClientTokenId"
            ) in caplog.text
        else:
            result = scheduler.future.result()
            assert result.job_id == "job-initial-auth"
            assert stages == ["collect", "preprocess", "convert"]
            if outcome == "recoverable":
                assert (
                    "Error in resource 'bucket_policy' during iteration" in caplog.text
                )
                assert "Error executing DLT defer" in caplog.text
        scheduler._handle_completed_job(scheduler.future)
        assert ends[0][0] == (
            "job-initial-auth",
            ManagedJobOutcome.FAILED if failed else ManagedJobOutcome.SUCCEEDED,
        )
        assert len(ends) == 1
        assert len(uploads) == (0 if failed else 1)
        assert identity_requests == (
            []
            if outcome == "assume-role-denied"
            else [ROLE_ACCESS_KEY if role else ACCESS_KEY]
        )
        assert not directory.exists()
        assert not output_directory.exists()
        assert scheduler._aws_profile_workspace is None
        assert scheduler.future is scheduler.job_running is None
        assert ACCESS_KEY not in caplog.text and SECRET_KEY not in caplog.text
        for stubber in stubbers:
            stubber.assert_no_pending_responses()
    finally:
        for stubber in stubbers:
            stubber.deactivate()
        scheduler.executor.shutdown(wait=True, cancel_futures=True)
        scheduler._worker_logging.close()
        scheduler._cleanup_aws_profile_workspace()


def test_boto3_file_provider_uses_exact_pair_without_a_session_token(
    workspace, monkeypatch
):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "runtime-access")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "runtime-secret")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "runtime-token")
    with managed_aws_profile({}, PAIR, workspace.directory) as profile:
        assert (
            dlt.secrets.get("sources.source.aws.credentials.profile_name", str)
            == profile
        )
        session = boto3.Session(profile_name=profile)
        _assert_source_credentials(session)
        sts = session.client("sts", region_name="us-east-1")
        with Stubber(sts) as stubber:
            stubber.add_response("get_caller_identity", _identity(), {})
            assert sts.get_caller_identity()["Account"] == SOURCE_ACCOUNT


def test_arbitrary_settings_survive_profile_adaptation_without_mutation(workspace):
    options = {
        "profile_name": "replaced-at-runtime",
        "profile_names": ["other-profile"],
        "auth_mode": "unknown-mode",
        "source_account": "incomplete-organization",
        "future": {"values": [None, {}, [], False, 0]},
        "role_arn": "not-an-arn",
        "role_session_name": "x",
        "external_id": "contains spaces",
    }
    params = {
        "sources": {
            "source": {"aws": {"credentials": options, "future": options["future"]}}
        },
        "pipeline": {"credentials": {"unknown": None}},
    }
    original = deepcopy(params)
    pair = AWSAccessKeyCredentials("nonstandard-key", "short%secret")
    with managed_aws_profile(params, pair, workspace.directory) as profile:
        document = dlt.config.get("sources.source.aws", dict)
        assert document == {
            "credentials": {**options, "profile_name": profile},
            "future": options["future"],
        }
        assert dlt.config.get("pipeline.credentials", dict) == {"unknown": None}
        config = ConfigParser(interpolation=None)
        config.read(workspace.directory / "config")
        role = config[f"profile {profile}"]
        assert role["role_arn"] == "not-an-arn"
        assert role["role_session_name"] == "x"
        assert role["external_id"] == "contains spaces"
        keys = ConfigParser(interpolation=None)
        keys.read(workspace.directory / "credentials")
        assert dict(keys[role["source_profile"]]) == {
            "aws_access_key_id": "nonstandard-key",
            "aws_secret_access_key": "short%secret",
        }
    assert params == original


@pytest.mark.parametrize(
    "settings",
    [
        {"role_arn": ROLE_ARN, "role_session_name": "collection-session"},
        {"role_arn": ROLE_ARN, "external_id": 123},
        {"role_arn": "user/not-a-role", "role_session_name": "x" * 65},
    ],
)
def test_role_fields_are_mechanically_serialized(workspace, settings):
    params = {"sources": {"source": {"aws": {"credentials": settings}}}}
    with managed_aws_profile(params, PAIR, workspace.directory) as profile:
        config = ConfigParser(interpolation=None)
        config.read(workspace.directory / "config")
        for key, value in settings.items():
            assert config[f"profile {profile}"][key] == str(value)
        assert config[f"profile {profile}"]["source_profile"] == f"{profile}-source"


@pytest.mark.parametrize("value", [None, {}, [], "unencodable-\ud800"])
def test_unserializable_ini_values_fail_operationally_and_cleanup(workspace, value):
    params = {
        "sources": {
            "source": {
                "aws": {"credentials": {"role_arn": ROLE_ARN, "external_id": value}}
            }
        }
    }
    original = dict(os.environ)
    with (
        pytest.raises(
            ManagedConfigurationError, match="could not be serialized"
        ) as error,
        managed_aws_profile(params, PAIR, workspace.directory),
    ):
        pytest.fail("unserializable INI cannot be installed")
    assert "None" not in str(error.value)
    assert dict(os.environ) == original
    assert not (workspace.directory / "credentials").exists()


@pytest.mark.parametrize("role_profile", [False, True], ids=["raw-user", "role"])
def test_partial_profile_write_failure_restores_worker_state(
    role_profile, workspace, monkeypatch, caplog
):
    original_environment = dict(os.environ)
    providers = Container()[PluggableRunContext].providers
    original_providers = providers.providers
    write_ini = aws_profile._write_ini

    def fail_config_write(path, sections):
        if path.name == "config":
            raise OSError(SECRET_KEY)
        write_ini(path, sections)

    monkeypatch.setattr(aws_profile, "_write_ini", fail_config_write)
    params = (
        {"sources": {"source": {"aws": {"credentials": {"role_arn": ROLE_ARN}}}}}
        if role_profile
        else {}
    )
    with (
        pytest.raises(ManagedConfigurationError) as error,
        managed_aws_profile(params, PAIR, workspace.directory),
    ):
        pytest.fail("partial profile cannot run collection")
    assert SECRET_KEY not in "".join(traceback.format_exception(error.value))
    assert "OSError: [REDACTED]" in caplog.text
    assert "fail_config_write" in caplog.text
    assert SECRET_KEY not in caplog.text
    assert not (workspace.directory / "credentials").exists()
    assert dict(os.environ) == original_environment
    assert providers.providers is original_providers


@pytest.mark.parametrize("wrapped", [False, True], ids=["direct", "wrapped"])
@pytest.mark.parametrize("sdk_error", [False, True], ids=["client", "botocore"])
def test_unlogged_sdk_failures_keep_safe_diagnostics(
    workspace, caplog, wrapped, sdk_error
):
    temporary_secret = "temporary-secret-not-in-raw-pair"
    payload = f"{ACCESS_KEY} {SECRET_KEY} {temporary_secret}"
    failure = (
        ParamValidationError(report=payload)
        if sdk_error
        else ClientError(
            {"Error": {"Code": "AccessDenied", "Message": payload}},
            "GetCallerIdentity",
        )
    )
    with (
        pytest.raises(ManagedConfigurationError) as error,
        caplog.at_level(logging.INFO),
        managed_aws_profile({}, PAIR, workspace.directory),
    ):
        try:
            raise failure
        except Exception as cause:
            if wrapped:
                raise RuntimeError(f"Collector failed: {payload}") from cause
            raise

    record = next(
        record
        for record in caplog.records
        if "Managed AWS collection failed" in record.getMessage()
    )
    assert record.levelno == logging.ERROR
    assert record.sdk_exception_type == type(failure).__name__
    assert "test_unlogged_sdk_failures_keep_safe_diagnostics" in record.getMessage()
    if sdk_error:
        assert "parameter validation failed" in record.getMessage()
    else:
        assert "GetCallerIdentity returned AccessDenied" in record.getMessage()
        assert "resource permissions" in record.getMessage()
    if wrapped:
        assert "RuntimeError" in record.getMessage()
    for value in (ACCESS_KEY, SECRET_KEY, temporary_secret):
        assert value not in caplog.text
        assert all(value not in repr(record.__dict__) for record in caplog.records)
        assert value not in "".join(traceback.format_exception(error.value))
    assert str(error.value) == "Managed AWS collection failed; check worker logs"
    assert not (workspace.directory / "credentials").exists()


def test_unlogged_import_failure_keeps_redacted_traceback(workspace, caplog):
    with (
        pytest.raises(ManagedConfigurationError),
        managed_aws_profile({}, PAIR, workspace.directory),
    ):
        raise ModuleNotFoundError(f"Missing extension for {ACCESS_KEY}: {SECRET_KEY}")

    assert "ModuleNotFoundError: Missing extension for [REDACTED]: [REDACTED]" in (
        caplog.text
    )
    assert "Traceback (most recent call last)" in caplog.text
    assert ACCESS_KEY not in caplog.text
    assert SECRET_KEY not in caplog.text


def test_sdk_debug_responses_are_suppressed_and_logging_state_restored(
    workspace, caplog
):
    sdk_logger = logging.getLogger("botocore.managed_profile_test")
    previous = (
        sdk_logger.level,
        sdk_logger.handlers[:],
        sdk_logger.disabled,
        sdk_logger.propagate,
    )
    sdk_logger.disabled = False
    sdk_logger.propagate = True
    sdk_logger.setLevel(logging.DEBUG)
    try:
        with (
            caplog.at_level(logging.DEBUG),
            managed_aws_profile({}, PAIR, workspace.directory),
        ):
            sdk_logger.debug(
                "AssumeRole SecretAccessKey=%s", "temporary-secret-not-in-raw-pair"
            )
            logging.getLogger("botocore.new_managed_profile_test").debug(
                "Response body %s", SECRET_KEY
            )
        assert "temporary-secret-not-in-raw-pair" not in caplog.text
        assert SECRET_KEY not in caplog.text
        assert sdk_logger.disabled is False
        assert sdk_logger.level == logging.DEBUG
    finally:
        sdk_logger.setLevel(previous[0])
        sdk_logger.handlers, sdk_logger.disabled, sdk_logger.propagate = previous[1:]


def test_profile_resolves_in_sdk_and_preserves_nested_configuration(workspace):
    params = {
        "sources": {
            "source": {
                "aws": {
                    "credentials": {
                        "region_name": "eu-west-1",
                        "endpoint_url": "https://aws.example.test",
                    },
                    "max_attempts": 7,
                    "bucket_policy": {
                        "include": True,
                        "max_objects": 9,
                        "rule": [{"include": True, "match_bucket": "public/.*"}],
                    },
                }
            }
        }
    }
    original = deepcopy(params)
    with managed_aws_profile(params, PAIR, workspace.directory) as profile:
        settings = dlt.config.get("sources.source.aws", dict)
        credentials = settings["credentials"]
        assert credentials == {
            **params["sources"]["source"]["aws"]["credentials"],
            "profile_name": profile,
        }
        session = boto3.Session(
            profile_name=credentials["profile_name"],
            region_name=credentials["region_name"],
        )
        _assert_source_credentials(session)
        client = session.client("sts", endpoint_url=credentials["endpoint_url"])
        with Stubber(client) as stubber:
            stubber.add_response("get_caller_identity", _identity(), {})
            assert client.get_caller_identity()["Account"] == SOURCE_ACCOUNT
        assert client.meta.endpoint_url == "https://aws.example.test"
        assert settings["max_attempts"] == 7
        assert (
            settings["bucket_policy"]
            == params["sources"]["source"]["aws"]["bucket_policy"]
        )
        for name in ("credentials", "config"):
            path = workspace.directory / name
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(workspace.directory.stat().st_mode) == 0o700
        assert (
            "aws_session_token" not in (workspace.directory / "credentials").read_text()
        )
        assert Container()[TraceLogContext].all_traces == []
    assert params == original
    assert not (workspace.directory / "credentials").exists()
    assert not (workspace.directory / "config").exists()


def test_runtime_and_local_configuration_cannot_override_generated_identity(
    workspace, monkeypatch
):
    for key, value in {
        "AWS_ACCESS_KEY_ID": "scheduler-access-key",
        "AWS_SECRET_ACCESS_KEY": "scheduler-secret",
        "AWS_SESSION_TOKEN": "scheduler-token",
        "AWS_SECURITY_TOKEN": "legacy-token",
        "AWS_PROFILE": "local-profile",
        "AWS_DEFAULT_PROFILE": "local-profile",
        "AWS_ROLE_ARN": "arn:aws:iam::123456789012:role/workload",
        "AWS_WEB_IDENTITY_TOKEN_FILE": "/must/not/be/read",
        "AWS_CONTAINER_CREDENTIALS_FULL_URI": "http://127.0.0.1:1/credentials",
        "SOURCES__SOURCE__AWS__CREDENTIALS__PROFILE_NAME": "local-profile",
        "SOURCES__SOURCE__AWS__CREDENTIALS__PROFILE_NAMES": '["local-profile"]',
        "SOURCES__SOURCE__AWS__CREDENTIALS__ORG_ROLE_NAME": "LocalRole",
        "CREDENTIALS__PROFILE_NAMES": '["local-profile"]',
        "DESTINATION__BLOODHOUNDENTERPRISE__URL": "https://bhe.example.test",
    }.items():
        monkeypatch.setenv(key, value)
    providers = Container()[PluggableRunContext].providers
    original_providers = providers.providers
    local = CustomLoaderDocProvider(
        "Local TOML equivalent",
        lambda: {
            "sources": {
                "source": {
                    "aws": {
                        "credentials": {
                            "profile_names": ["local-profile"],
                            "org_role_name": "LocalRole",
                        }
                    }
                }
            }
        },
    )

    class UnsectionedDefaults(ConfigProvider):
        def get_value(self, key, hint, pipeline_name, *sections):
            return {
                "profile_names": ["global-profile"],
                "org_role_name": "GlobalRole",
            }.get(key), key

        name = "Global source credential defaults"
        supports_secrets = True
        supports_sections = False

    providers.providers = [*original_providers, local, UnsectionedDefaults()]
    original_environment = dict(os.environ)
    try:
        with managed_aws_profile({}, PAIR, workspace.directory) as profile:
            assert (
                dlt.secrets.get("sources.source.aws.credentials.profile_name", str)
                == profile
            )
            assert (
                dlt.secrets.get(
                    "sources.source.aws.credentials.profile_names", list[str]
                )
                is None
            )
            assert (
                dlt.secrets.get("sources.source.aws.credentials.org_role_name", str)
                is None
            )
            assert dlt.secrets.get("credentials.profile_names", list[str]) is None
            _assert_source_credentials(boto3.Session(profile_name=profile))
            assert (
                dlt.config.get("destination.bloodhoundenterprise.url", str)
                == "https://bhe.example.test"
            )
        assert dict(os.environ) == original_environment
    finally:
        providers.providers = original_providers


@pytest.mark.parametrize(
    "role_profile", [False, True], ids=["raw-user", "initial-role"]
)
@pytest.mark.parametrize(
    "dedicated_source", [False, True], ids=["management-profile", "source-account"]
)
def test_organization_settings_pass_through_with_generated_source_profile(
    dedicated_source,
    role_profile,
    workspace,
    profile_assumptions,
):
    options = {
        "org_role_name": "MemberCollectorRole",
        "org_role_session_name": "job-session",
        "include_management_account": False,
        "region_name": "us-west-2",
    }
    if dedicated_source:
        options.update(
            source_account=SOURCE_ACCOUNT,
            management_account_id=MANAGEMENT_ACCOUNT,
            management_role_name="ManagementCollectorRole",
        )
    if role_profile:
        options.update(
            role_arn=f"arn:aws:iam::{SOURCE_ACCOUNT}:role/InitialCollectionRole",
            role_session_name="initial-session",
        )
        profile_assumptions(
            {"RoleArn": options["role_arn"], "RoleSessionName": "initial-session"}
        )
    params = {"sources": {"source": {"aws": {"credentials": options}}}}
    original = deepcopy(params)
    with managed_aws_profile(params, PAIR, workspace.directory) as profile:
        settings = dlt.secrets.get("sources.source.aws.credentials", dict)
        assert settings == {**options, "profile_name": profile}
        session = boto3.Session(
            profile_name=settings["profile_name"], region_name=settings["region_name"]
        )
        if role_profile:
            _assert_role_credentials(session)
        else:
            _assert_source_credentials(session)
    assert params == original


@pytest.mark.parametrize(
    "role_profile", [False, True], ids=["raw-user", "role-then-raw"]
)
@pytest.mark.parametrize("failed", [False, True], ids=["success", "failure"])
def test_successive_jobs_restore_environment_providers_and_remove_files(
    failed, role_profile, profile_assumptions, monkeypatch
):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "scheduler-access")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "scheduler-secret")
    scheduler_session = boto3.Session()
    assert scheduler_session.get_credentials().method == "env"
    original_environment = dict(os.environ)
    providers = Container()[PluggableRunContext].providers
    original_providers = providers.providers
    original_traces = Container()[TraceLogContext]
    paths, profiles = [], []
    for index in range(2):
        workspace = AWSProfileWorkspace()
        paths.append(workspace.directory)
        options = (
            {
                "sources": {
                    "source": {"aws": {"credentials": {"region_name": "eu-west-1"}}}
                }
            }
            if index == 0
            else {}
        )
        if role_profile and index == 0:
            options["sources"]["source"]["aws"]["credentials"]["role_arn"] = ROLE_ARN
        try:
            with managed_aws_profile(options, PAIR, workspace.directory) as profile:
                profiles.append(profile)
                settings = dlt.secrets.get("sources.source.aws.credentials", dict)
                assert settings["profile_name"] == profile
                assert settings.get("region_name") == (
                    "eu-west-1" if index == 0 else None
                )
                session = boto3.Session(
                    profile_name=settings["profile_name"],
                    region_name=settings.get("region_name"),
                )
                if role_profile and index == 0:
                    profile_assumptions(
                        {"RoleArn": ROLE_ARN, "RoleSessionName": profile}
                    )
                    _assert_role_credentials(session)
                else:
                    _assert_source_credentials(session)
                if failed and index == 0:
                    raise RuntimeError(SECRET_KEY)
        except ManagedConfigurationError as error:
            assert failed and index == 0
            assert SECRET_KEY not in "".join(traceback.format_exception(error))
        finally:
            assert not (workspace.directory / "credentials").exists()
            workspace.cleanup()
        assert dict(os.environ) == original_environment
        assert providers.providers is original_providers
        assert Container()[TraceLogContext] is original_traces
        assert boto3.Session().get_credentials().access_key == "scheduler-access"
        assert scheduler_session.get_credentials().access_key == "scheduler-access"
    assert len(set(paths)) == len(set(profiles)) == 2
    assert all(not path.exists() for path in paths)


def test_worker_logs_errors_and_support_bundle_are_redacted(
    workspace, tmp_path, caplog
):
    log_path = tmp_path / "logs"
    log_path.mkdir()
    handler = logging.FileHandler(log_path / "openhound.log")
    handler.setFormatter(openhound_logging.OpenHoundJSONFormatter())
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        with (
            pytest.raises(ManagedConfigurationError) as error,
            caplog.at_level(logging.DEBUG),
            managed_aws_profile({}, PAIR, workspace.directory),
        ):
            logger = logging.getLogger("openhound_aws.auth")
            logger.info(
                "Credential failure: %s",
                SECRET_KEY,
                extra={"response": {"credential": SECRET_KEY}},
            )
            try:
                raise RuntimeError(f"AccessDenied for {ACCESS_KEY}: {SECRET_KEY}")
            except RuntimeError:
                logger.exception("SDK failure")
            # Exercise a handler configured after the adapter was entered.
            late_handler = openhound_logging._protect_handler(
                logging.FileHandler(log_path / "ext_aws.log")
            )
            root.addHandler(late_handler)
            try:
                logger.error("Late handler %s", SECRET_KEY)
            finally:
                root.removeHandler(late_handler)
                late_handler.close()
            raise RuntimeError(SECRET_KEY)
        assert SECRET_KEY not in "".join(traceback.format_exception(error.value))
        assert SECRET_KEY not in caplog.text
        assert ACCESS_KEY not in caplog.text
        assert all(SECRET_KEY not in repr(record.__dict__) for record in caplog.records)
        assert SECRET_KEY not in repr(PAIR)
        assert "RuntimeError: AccessDenied" in (log_path / "openhound.log").read_text()
        bundle = create_support_bundle("aws", log_path)
        try:
            with zipfile.ZipFile(bundle) as archive:
                assert set(archive.namelist()) == {"openhound.log", "ext_aws.log"}
                assert all(
                    SECRET_KEY.encode() not in archive.read(name)
                    for name in archive.namelist()
                )
                assert b"RuntimeError: AccessDenied" in archive.read("openhound.log")
        finally:
            bundle.unlink()
            bundle.parent.rmdir()
    finally:
        root.removeHandler(handler)
        handler.close()


def _blocked_collection(collector, job_id, params, credentials, managed, directory):
    with managed_aws_profile(params, credentials, directory):
        time.sleep(60)


def _wait_for_credentials(directory):
    deadline = time.monotonic() + 15
    while not (directory / "config").exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert (directory / "credentials").is_file()
    assert (directory / "config").is_file()


@pytest.mark.parametrize(
    "claim_lost", [False, True], ids=["abrupt-death", "scheduler-kill"]
)
def test_parent_removes_credentials_after_forced_worker_termination(
    claim_lost, monkeypatch
):
    scheduler = service.Service(
        "https://bhe.example.test", "token", "id", "aws", managed=True
    )
    monkeypatch.setattr(scheduler.client, "start_collector_job", lambda job_id: None)
    ends = []
    monkeypatch.setattr(
        scheduler.client,
        "end_managed_job",
        lambda *args, **kwargs: ends.append((args, kwargs)),
    )
    executor = scheduler.executor
    submit = executor.submit
    monkeypatch.setattr(
        executor, "submit", lambda function, *args: submit(_blocked_collection, *args)
    )
    try:
        scheduler.run_claimed_job("job", {}, PAIR)
        directory = scheduler._aws_profile_workspace.directory
        _wait_for_credentials(directory)
        assert SECRET_KEY in (directory / "credentials").read_text()
        workers = list(executor._processes.values())
        if claim_lost:
            scheduler._abort_managed_job("job")
            assert ends == []
        else:
            for worker in workers:
                worker.kill()
                worker.join(timeout=10)
            scheduler.future.exception(timeout=10)
            scheduler._handle_completed_job(scheduler.future)
            assert (
                ends[0][1]["failure_message"]
                == "Collection worker for 'aws' was terminated abruptly"
            )
        assert all(not worker.is_alive() for worker in workers)
        assert not directory.exists()
        assert scheduler._aws_profile_workspace is None
    finally:
        for worker in list((executor._processes or {}).values()):
            if worker.is_alive():
                worker.kill()
        executor.shutdown(wait=True, cancel_futures=True)
        scheduler.executor.shutdown(wait=True, cancel_futures=True)
        scheduler._worker_logging.close()
        scheduler._cleanup_aws_profile_workspace()


def _orphan_scheduler(temp_root, connection):
    aws_profile.tempfile.tempdir = temp_root
    workspace = AWSProfileWorkspace()
    with managed_aws_profile({}, PAIR, workspace.directory):
        connection.send(workspace.directory)
        time.sleep(60)


def test_startup_reclaims_orphans_after_scheduler_death(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe()
    process = ctx.Process(target=_orphan_scheduler, args=(str(tmp_path), child))
    process.start()
    try:
        assert parent.poll(15)
        directory = parent.recv()
        cleanup_orphaned_aws_profiles()
        assert directory.exists()  # Both process-held locks are still live.
        process.kill()
        process.join(timeout=10)
        assert not process.is_alive()
        cleanup_orphaned_aws_profiles()
        assert not directory.exists()
    finally:
        if process.is_alive():
            process.kill()
        process.join(timeout=10)
        parent.close()
        child.close()


def test_orphan_reaper_preserves_live_worker_after_scheduler_death(workspace):
    executor = ProcessPoolExecutor(max_workers=1, max_tasks_per_child=1)
    try:
        future = executor.submit(
            _blocked_collection, "aws", "job", {}, PAIR, True, workspace.directory
        )
        _wait_for_credentials(workspace.directory)
        workspace._owner.release()  # Simulate the scheduler's OS lock being released.
        cleanup_orphaned_aws_profiles()
        assert (workspace.directory / "credentials").exists()
        for worker in list(executor._processes.values()):
            worker.kill()
            worker.join(timeout=10)
        future.exception(timeout=10)
        cleanup_orphaned_aws_profiles()
        assert not workspace.directory.exists()
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
