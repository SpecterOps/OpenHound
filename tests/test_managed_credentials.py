import io
import logging
import traceback
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError, ParamValidationError

from openhound.scheduler import credentials
from openhound.scheduler.configuration import ManagedConfigurationError


@pytest.mark.parametrize("identifier", [None, "external-reference"])
def test_configuration_secrets_use_shared_preparation_for_any_extension(identifier):
    job = SimpleNamespace(
        job_key="example-extension",
        job_type_id=7,
        secret_key_id="secret-reference",
        external_secret_key_id=identifier,
        params={"source": {"enabled": True}},
    )
    document = {"source": {"credentials": {"token": "private-token"}}}
    requested = []

    def load_secret(reference):
        requested.append(reference)
        return document

    secrets, environment = credentials.prepare_job_credentials(job, load_secret)

    assert requested == ["secret-reference"]
    assert secrets == document
    assert environment == {}


@pytest.mark.parametrize("job_type_id", [7, 9999])
def test_temporary_aws_binding_uses_job_key_independently_of_numeric_type(job_type_id):
    job = SimpleNamespace(
        job_key="aws",
        job_type_id=job_type_id,
        secret_key_id="secret-reference",
        external_secret_key_id="credential-identifier",
        params={"source": {"enabled": True}},
    )

    secrets, environment = credentials.prepare_job_credentials(
        job, lambda reference: "credential-secret"
    )

    assert secrets == {}
    assert environment["AWS_ACCESS_KEY_ID"] == "credential-identifier"
    assert environment["AWS_SECRET_ACCESS_KEY"] == "credential-secret"
    assert environment["AWS_PROFILE"] is None
    assert environment["AWS_SESSION_TOKEN"] is None


def test_credential_preparation_uses_declared_names_for_another_extension(monkeypatch):
    defaults = {"EXAMPLE_INHERITED_TOKEN": None}
    monkeypatch.setitem(
        credentials._TEMPORARY_ENVIRONMENT_BINDINGS,
        "example-extension",
        ("EXAMPLE_CREDENTIAL_ID", "EXAMPLE_CREDENTIAL_VALUE", defaults),
    )
    job = SimpleNamespace(
        job_key="example-extension",
        secret_key_id=" reference with spaces ",
        external_secret_key_id="opaque-identifier",
        params={"source": {"enabled": True}},
    )
    requested = []

    def load_secret(reference):
        requested.append(reference)
        return "opaque secret value"

    secrets, environment = credentials.prepare_job_credentials(job, load_secret)

    assert requested == [" reference with spaces "]
    assert secrets == {}
    assert environment == {
        "EXAMPLE_CREDENTIAL_ID": "opaque-identifier",
        "EXAMPLE_CREDENTIAL_VALUE": "opaque secret value",
        "EXAMPLE_INHERITED_TOKEN": None,
    }
    assert defaults == {"EXAMPLE_INHERITED_TOKEN": None}


@pytest.mark.parametrize("wrapped", [False, True], ids=["direct", "wrapped"])
@pytest.mark.parametrize("sdk_error", [False, True], ids=["client", "botocore"])
def test_collection_sdk_failures_keep_safe_diagnostics(caplog, wrapped, sdk_error):
    supplied_key = "supplied-example-key"
    supplied_secret = "supplied-example-secret"
    temporary_secret = "temporary-secret-not-in-supplied-pair"
    payload = f"{supplied_key} {supplied_secret} {temporary_secret}"
    failure = (
        ParamValidationError(report=payload)
        if sdk_error
        else ClientError(
            {"Error": {"Code": "AccessDenied", "Message": payload}}, "AssumeRole"
        )
    )

    with (
        caplog.at_level(logging.DEBUG),
        pytest.raises(ManagedConfigurationError) as error,
        credentials.managed_collection_configuration({"custom": {"enabled": True}}, {}),
    ):
        try:
            raise failure
        except Exception as cause:
            if wrapped:
                raise RuntimeError(f"Collector failed: {payload}") from cause
            raise

    record = next(
        r for r in caplog.records if "Managed collection failed" in r.getMessage()
    )
    assert record.levelno == logging.ERROR
    assert record.sdk_exception_type == type(failure).__name__
    assert "test_collection_sdk_failures_keep_safe_diagnostics" in record.getMessage()
    assert (
        "parameter validation failed"
        if sdk_error
        else "AssumeRole returned AccessDenied"
    ) in record.getMessage()
    if wrapped:
        assert "RuntimeError" in record.getMessage()
    for value in (supplied_key, supplied_secret, temporary_secret):
        assert value not in caplog.text
        assert all(value not in repr(r.__dict__) for r in caplog.records)
        assert value not in "".join(traceback.format_exception(error.value))
    assert str(error.value) == "Managed collection failed; check worker logs"


@pytest.mark.parametrize("failed", [False, True], ids=["success", "failure"])
def test_collection_suppresses_sdk_debug_handlers_and_restores_logging(
    monkeypatch, caplog, failed
):
    sdk_logger = logging.getLogger("botocore.managed_credentials_test")
    with io.StringIO() as output:
        handler = logging.StreamHandler(output)
        monkeypatch.setattr(sdk_logger, "level", logging.DEBUG)
        monkeypatch.setattr(sdk_logger, "handlers", [handler])
        monkeypatch.setattr(sdk_logger, "propagate", False)
        monkeypatch.setattr(sdk_logger, "disabled", False)
        sdk_logger.setLevel(logging.DEBUG)
        loggers = [
            logging.getLogger("boto3"),
            logging.getLogger("botocore"),
            sdk_logger,
        ]
        previous = {
            value: (value.level, value.handlers[:], value.propagate, value.disabled)
            for value in loggers
        }
        temporary_secret = "unlisted-temporary-sdk-secret"

        with (
            caplog.at_level(logging.DEBUG),
            pytest.raises(ManagedConfigurationError) if failed else nullcontext(),
            credentials.managed_collection_configuration(
                {"custom": {"enabled": True}}, {}
            ),
        ):
            sdk_logger.debug("AssumeRole SecretAccessKey=%s", temporary_secret)
            logging.getLogger("botocore.managed_credentials_test.new_child").debug(
                "Response body %s", temporary_secret
            )
            if failed:
                raise RuntimeError("collection failed")

        assert output.getvalue() == ""
        assert temporary_secret not in caplog.text
        for value, state in previous.items():
            assert (
                value.level,
                value.handlers,
                value.propagate,
                value.disabled,
            ) == state
        sdk_logger.debug("logging restored")
        assert "logging restored" in output.getvalue()
        handler.close()
