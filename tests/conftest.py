import importlib
import importlib.util
import os
import sys
from pathlib import Path
from typing import Union

import dlt
import pytest


def import_from_path(file_path: Path) -> None:
    module_name = file_path.stem
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)  # type: ignore
    spec.loader.exec_module(module)  # type: ignore


def pytest_configure(config):
    repo_root = Path(__file__).resolve().parents[1]
    sources_root = repo_root / "openhound" / "sources"
    py_files = [path for path in sources_root.rglob("**/models/*.py") if path.is_file()]
    for module in py_files:
        import_from_path(module)


@pytest.fixture(scope="session")
def aws_extension_auth():
    """Load the real extension models/auth, without its unrelated native engine.

    Use the sibling repo, OPENHOUND_AWS_SOURCE, or an installed extension. These
    model integration tests are optional when the internal extension is absent.
    """
    default = Path(__file__).resolve().parents[2] / "openhound-aws"
    repo = Path(os.environ.get("OPENHOUND_AWS_SOURCE", default))
    path = repo / "src" / "openhound_aws" / "auth.py"
    if not path.is_file():
        if "OPENHOUND_AWS_SOURCE" in os.environ:
            pytest.fail("OPENHOUND_AWS_SOURCE must point to an openhound-aws checkout")
        return pytest.importorskip(
            "openhound_aws.auth",
            reason="AWS model integration requires openhound-aws or OPENHOUND_AWS_SOURCE",
        )
    name = "_openhound_aws_auth_integration"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def resolve_aws_settings(aws_extension_auth):
    auth = aws_extension_auth
    observed = {}

    # Use the real model union and DLT source resolution used by the extension.
    # Resolving the configuration alone does not need its Rust policy evaluator.
    @dlt.source(name="aws", section="source")
    def source(
        credentials: Union[  # noqa: UP007 - match the extension source's union
            auth.AWSOrganizationProfileCredentials,
            auth.AWSMultiProfileCredentials,
            auth.AWSProfileCredentials,
        ] = dlt.secrets.value,
        bucket_policy: auth.BucketPolicyConfiguration = dlt.config.value,
        max_attempts: int = 3,
    ):
        observed.update(
            credentials=credentials,
            bucket_policy=bucket_policy,
            max_attempts=max_attempts,
        )
        return dlt.resource([], name="configuration_only")

    def resolve():
        source()
        return observed.copy()

    return resolve
