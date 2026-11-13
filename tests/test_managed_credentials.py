from types import SimpleNamespace

import pytest

from openhound.scheduler import credentials


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
