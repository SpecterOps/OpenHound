import logging
import multiprocessing
import os
import stat
import tempfile
import time
import traceback
import zipfile
from concurrent.futures import ProcessPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import boto3
import dlt
import pytest
from botocore.httpsession import URLLib3Session
from botocore.stub import Stubber
from dlt.common.configuration.container import Container
from dlt.common.configuration.providers.doc import CustomLoaderDocProvider
from dlt.common.configuration.providers.provider import ConfigProvider
from dlt.common.configuration.specs.pluggable_run_context import PluggableRunContext
from dlt.common.configuration.utils import TraceLogContext

from openhound.core import logging as openhound_logging
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
MEMBER_ACCOUNT = "222222222222"


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


def test_partial_profile_write_failure_restores_worker_state(workspace, monkeypatch):
    original_environment = dict(os.environ)
    providers = Container()[PluggableRunContext].providers
    original_providers = providers.providers
    write_ini = aws_profile._write_ini

    def fail_config_write(path, sections):
        if path.name == "config":
            raise OSError(SECRET_KEY)
        write_ini(path, sections)

    monkeypatch.setattr(aws_profile, "_write_ini", fail_config_write)
    with (
        pytest.raises(ManagedConfigurationError) as error,
        managed_aws_profile({}, PAIR, workspace.directory),
    ):
        pytest.fail("partial profile cannot run collection")
    assert SECRET_KEY not in "".join(traceback.format_exception(error.value))
    assert not (workspace.directory / "credentials").exists()
    assert dict(os.environ) == original_environment
    assert providers.providers is original_providers


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


def test_real_sdk_and_extension_profile_resolution(
    workspace, aws_extension_auth, resolve_aws_settings
):
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
        settings = resolve_aws_settings()
        model = settings["credentials"]
        assert type(model) is aws_extension_auth.AWSProfileCredentials
        assert model.profile_name == profile
        assert model.region_name == "eu-west-1"
        assert model.endpoint_url == "https://aws.example.test"
        _assert_source_credentials(model.session)
        client = model.session.client("sts", endpoint_url=model.endpoint_url)
        with Stubber(client) as stubber:
            stubber.add_response("get_caller_identity", _identity(), {})
            assert client.get_caller_identity()["Account"] == SOURCE_ACCOUNT
        assert client.meta.endpoint_url == "https://aws.example.test"
        assert settings["max_attempts"] == 7
        bucket = settings["bucket_policy"]
        assert bucket.include is True and bucket.max_objects == 9
        assert bucket.bucket_whitelist[0].match == "public/.*"
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
    workspace, resolve_aws_settings, aws_extension_auth, monkeypatch
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
            model = resolve_aws_settings()["credentials"]
            assert type(model) is aws_extension_auth.AWSProfileCredentials
            assert model.profile_name == profile
            _assert_source_credentials(model.session)
            assert (
                dlt.config.get("destination.bloodhoundenterprise.url", str)
                == "https://bhe.example.test"
            )
        assert dict(os.environ) == original_environment
    finally:
        providers.providers = original_providers


@pytest.mark.parametrize(
    "dedicated_source", [False, True], ids=["management-profile", "source-account"]
)
def test_organization_models_and_assume_role_use_generated_source_identity(
    dedicated_source, workspace, aws_extension_auth, resolve_aws_settings, monkeypatch
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
    params = {"sources": {"source": {"aws": {"credentials": options}}}}
    clients = []
    sessions = []
    original_client = boto3.session.Session.client

    def stub_sts(session, name, **kwargs):
        assert name == "sts"
        _assert_source_credentials(session)
        sessions.append(session)
        client = original_client(session, name, **kwargs)
        stubber = Stubber(client)
        if not clients:
            stubber.add_response("get_caller_identity", _identity(), {})
        else:
            account, role = (
                (MANAGEMENT_ACCOUNT, "ManagementCollectorRole")
                if dedicated_source and len(clients) == 1
                else (MEMBER_ACCOUNT, "MemberCollectorRole")
            )
            stubber.add_response(
                "assume_role",
                {
                    "Credentials": {
                        "AccessKeyId": "ASIAIOSFODNN7EXAMPLE",
                        "SecretAccessKey": "T" * 40,
                        "SessionToken": "assumed-session-token",
                        "Expiration": datetime.now(UTC) + timedelta(hours=1),
                    }
                },
                {
                    "RoleArn": f"arn:aws:iam::{account}:role/{role}",
                    "RoleSessionName": "job-session",
                },
            )
        stubber.activate()
        clients.append(stubber)
        return client

    monkeypatch.setattr(boto3.session.Session, "client", stub_sts)
    try:
        with managed_aws_profile(params, PAIR, workspace.directory) as profile:
            model = resolve_aws_settings()["credentials"]
            assert type(model) is aws_extension_auth.AWSOrganizationProfileCredentials
            assert model.profile_name == profile
            assert model.org_role_name == "MemberCollectorRole"
            assert model.collect_management_account is False
            auth = aws_extension_auth.AWSAuth(model)
            assert auth.source_ctx.account_id == SOURCE_ACCOUNT
            if dedicated_source:
                management = auth.management_ctx
                assert management.account_id == MANAGEMENT_ACCOUNT
                assert (
                    management.session.get_credentials().token
                    == "assumed-session-token"
                )
            member = auth.context_for_account(MEMBER_ACCOUNT)
            assert member.account_id == MEMBER_ACCOUNT
            assert member.session.get_credentials().token == "assumed-session-token"
            # Both management and member assumptions originate from the raw pair,
            # rather than chaining through management's temporary credentials.
            assert all(session is auth.source_ctx.session for session in sessions)
            for stubber in clients:
                stubber.assert_no_pending_responses()
    finally:
        for stubber in clients:
            stubber.deactivate()


@pytest.mark.parametrize("failed", [False, True], ids=["success", "failure"])
def test_successive_jobs_restore_environment_providers_and_remove_files(
    failed, resolve_aws_settings, monkeypatch
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
        try:
            with managed_aws_profile(options, PAIR, workspace.directory) as profile:
                profiles.append(profile)
                model = resolve_aws_settings()["credentials"]
                assert model.region_name == ("eu-west-1" if index == 0 else "us-east-1")
                _assert_source_credentials(model.session)
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
                raise RuntimeError(SECRET_KEY)
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
        assert all(SECRET_KEY not in repr(record.__dict__) for record in caplog.records)
        assert SECRET_KEY not in repr(PAIR)
        bundle = create_support_bundle("aws", log_path)
        try:
            with zipfile.ZipFile(bundle) as archive:
                assert set(archive.namelist()) == {"openhound.log", "ext_aws.log"}
                assert all(
                    SECRET_KEY.encode() not in archive.read(name)
                    for name in archive.namelist()
                )
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
