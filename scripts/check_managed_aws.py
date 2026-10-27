"""Exercise managed AWS profile creation in the bundle image with a local secret.

Secrets Manager is replaced at its SDK boundary. The credential parser, profile
writer, worker, extension source/configuration, and boto3 file provider are real.
"""

import argparse
import json
import os
import shutil
import signal
import stat
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import boto3
from botocore.session import Session as BotocoreSession
from botocore.stub import Stubber

from openhound.core.clients.aws_secrets_manager import AWSSecretsManager
from openhound.core.clients.models.jobs import CollectorJob
from openhound.scheduler import service
from openhound.scheduler.aws_profile import (
    AWSAccessKeyCredentials,
    AWSProfileWorkspace,
    prepare_aws_credentials,
)

SECRET_REFERENCE = "local/customer/openhound/profile-check"
OFFLINE_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"
OFFLINE_SECRET_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"


class LocalSecretsManager:
    """Return the same binary payload shape BHE writes, without an AWS request."""

    def __init__(self, secret: str):
        self.secret = secret
        self.requests: list[str] = []

    def get_secret_value(self, *, SecretId: str) -> dict:
        if SecretId != SECRET_REFERENCE:
            raise ValueError("Unexpected local secret reference")
        self.requests.append(SecretId)
        return {"SecretBinary": self.secret.encode("utf-8")}

    def batch_get_secret_value(self, *, SecretIdList: list[str]) -> dict:
        raise AssertionError("The managed AWS worker should read one job secret")


def local_job(access_key_id: str, params: dict) -> CollectorJob:
    now = datetime.now(UTC)
    return CollectorJob(
        id=uuid4(),
        job_schedule_id=None,
        job_profile_id=None,
        job_type_id=1,
        job_key="aws",
        version="v0.5.0",
        params=params,
        scope_client_id=None,
        external_secret_key_id=access_key_id,
        secret_key_id=SECRET_REFERENCE,
        priority=1,
        status="claimed",
        run_at=now,
        unclaimed_deadline_at=now + timedelta(days=1),
        attempts=0,
        max_attempts=1,
        last_failure=None,
        claimed_by=uuid4(),
        claimed_at=now,
        claim_expires_at=now + timedelta(minutes=15),
        created_at=now,
        updated_at=now,
    )


def check_profile(
    model,
    expected: AWSAccessKeyCredentials,
    directory: Path,
    *,
    role_arn: str | None = None,
) -> None:
    """Inspect the model DLT actually passed to the extension's AWSAuth."""
    profile = getattr(model, "profile_name", None)
    if not profile or not profile.startswith("openhound-job-"):
        print(
            f"OBSERVED: {type(model).__name__} selected without the generated profile; "
            "continuing with the extension's resolved credentials",
            flush=True,
        )
        return
    session = model.session
    credentials = session.get_credentials()
    method = "assume-role" if role_arn else "shared-credentials-file"
    if credentials is None or credentials.method != method:
        raise RuntimeError("The extension did not use the expected credential provider")
    if role_arn:
        config = session._session.get_scoped_config()
        if config.get("role_arn") != role_arn or not config.get("source_profile"):
            raise RuntimeError(
                "The generated profile does not select the requested role"
            )
        # Inspect the source pair without forcing an extra initial AssumeRole call.
        credentials = boto3.Session(
            profile_name=config["source_profile"]
        ).get_credentials()
        if credentials is None or credentials.method != "shared-credentials-file":
            raise RuntimeError(
                "The role did not reference the generated source profile"
            )
    frozen = credentials.get_frozen_credentials()
    if (frozen.access_key, frozen.secret_key, frozen.token) != (
        expected.access_key_id,
        expected.secret_access_key,
        None,
    ):
        raise RuntimeError("The extension resolved a different credential pair")
    for name in ("credentials", "config"):
        if stat.S_IMODE((directory / name).stat().st_mode) != 0o600:
            raise RuntimeError("Generated profile files must have mode 0600")
    print(
        f"PASS: {type(model).__name__} resolved {profile} "
        f"using {method}; supplied {'source ' if role_arn else ''}pair matches; files are 0600",
        flush=True,
    )


def run(job: CollectorJob, secret: str, *, probe: bool, offline: bool, output: Path):
    local_secrets = LocalSecretsManager(secret)
    reader = AWSSecretsManager(client=local_secrets)
    credentials = prepare_aws_credentials(job, reader.get_secret)
    if not isinstance(credentials, AWSAccessKeyCredentials):
        raise TypeError("This check requires the managed raw access-key contract")
    if local_secrets.requests != [SECRET_REFERENCE]:
        raise RuntimeError("Expected exactly one mocked Secrets Manager read")
    print(
        "PASS: job secret loaded through the real Secrets Manager payload parser",
        flush=True,
    )

    workspace = AWSProfileWorkspace()
    role_arn = (
        job.params.get("sources", {})
        .get("source", {})
        .get("aws", {})
        .get("credentials", {})
        .get("role_arn")
    )
    result = None
    previous_sigint = signal.getsignal(signal.SIGINT)
    try:
        with ExitStack() as stack:
            # Import the extension only after the worker installs job configuration.
            original_import = service.import_module
            observed_auth = []

            def load_source(name):
                module = original_import(name)
                if name == "openhound_aws.source":
                    real_auth = module.AWSAuth

                    def checked_auth(model, **kwargs):
                        check_profile(
                            model, credentials, workspace.directory, role_arn=role_arn
                        )
                        auth = real_auth(model, **kwargs)
                        observed_auth.append(auth)
                        return auth

                    stack.enter_context(patch.object(module, "AWSAuth", checked_auth))
                return module

            stack.enter_context(patch.object(service, "import_module", load_source))

            if probe:

                def check_authentication(extension, output_path):
                    extension.dlt_source()
                    if not observed_auth:
                        raise RuntimeError("The extension did not initialize AWSAuth")
                    # The actual extension authenticates its source profile with STS.
                    context = observed_auth[-1].source_ctx
                    # Stubbed SDK calls bypass signing; explicitly resolve the role
                    # credentials so offline checks still exercise AssumeRole.
                    context.session.get_credentials().get_frozen_credentials()
                    print(
                        f"PASS: extension STS identity is account {context.account_id}",
                        flush=True,
                    )
                    identity = context.session.client(
                        "sts", endpoint_url=context.endpoint_url
                    ).get_caller_identity()
                    print(
                        f"PASS: extension caller ARN is {identity['Arn']}", flush=True
                    )
                    return {"profile_check": [context.account_id]}

                stack.enter_context(
                    patch.object(service.dataflow, "pipeline", check_authentication)
                )

            if offline:
                real_client = BotocoreSession.create_client
                offline_stubbers = []

                def stub_sts(session, name, *args, **kwargs):
                    if name != "sts":
                        raise RuntimeError(
                            "Offline check attempted an unexpected AWS service"
                        )
                    client = real_client(session, name, *args, **kwargs)
                    stubber = stack.enter_context(Stubber(client))
                    offline_stubbers.append(stubber)
                    config = session.get_scoped_config()
                    if (
                        role_arn
                        and kwargs.get("aws_access_key_id") == credentials.access_key_id
                    ):
                        expected = {
                            "RoleArn": role_arn,
                            "RoleSessionName": config["role_session_name"],
                        }
                        if "external_id" in config:
                            expected["ExternalId"] = config["external_id"]
                        stubber.add_response(
                            "assume_role",
                            {
                                "Credentials": {
                                    "AccessKeyId": "ASIAIOSFODNN7EXAMPLE",
                                    "SecretAccessKey": "T" * 40,
                                    "SessionToken": "offline-role-token",
                                    "Expiration": datetime.now(UTC)
                                    + timedelta(hours=1),
                                }
                            },
                            expected,
                        )
                        return client
                    account = role_arn.split(":")[4] if role_arn else "123456789012"
                    arn = (
                        f"arn:{role_arn.split(':')[1]}:sts::{account}:assumed-role/"
                        f"{role_arn.rsplit('/', 1)[1]}/{config['role_session_name']}"
                        if role_arn
                        else f"arn:aws:iam::{account}:user/offline-profile-check"
                    )
                    stubber.add_response(
                        "get_caller_identity",
                        {
                            "Account": account,
                            "Arn": arn,
                            "UserId": "offline-profile-check",
                        },
                        {},
                    )
                    return client

                stack.enter_context(
                    patch.object(BotocoreSession, "create_client", stub_sts)
                )

            result = service._subprocess_collect(
                "aws", str(job.id), job.params, credentials, True, workspace.directory
            )
            if offline:
                for stubber in offline_stubbers:
                    stubber.assert_no_pending_responses()
            if not observed_auth:
                raise RuntimeError(
                    "The collection did not initialize AWS authentication"
                )
            if not probe:
                if not result.data_files:
                    raise RuntimeError(
                        "Collection completed without any OpenGraph JSON files"
                    )
                # Use a separate directory per run; existing output is preserved.
                destination = output / str(job.id)
                destination.mkdir(parents=True, exist_ok=False)
                for path in result.data_files:
                    shutil.copy2(path, destination / path.name)
                print(
                    f"PASS: collection produced {len(result.data_files)} JSON files in {destination}",
                    flush=True,
                )
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        try:
            workspace.cleanup()
        finally:
            if result is not None and result.output_path is not None:
                shutil.rmtree(result.output_path)
    if workspace.directory.exists():
        raise RuntimeError("Temporary profile workspace was not removed")
    print("PASS: temporary AWS profile workspace removed", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--offline",
        action="store_true",
        help="Use example keys and stub STS; no collection or network required",
    )
    mode.add_argument(
        "--auth-only",
        action="store_true",
        help="Resolve the real extension source and call live STS, without collecting",
    )
    parser.add_argument(
        "--credentials-file",
        type=Path,
        help="JSON containing access_key_id and secret_access_key",
    )
    parser.add_argument(
        "--params-file",
        type=Path,
        help="Nested managed-job configuration JSON (default: empty settings)",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("/tmp/managed-aws-output")
    )
    args = parser.parse_args()
    if args.offline and args.credentials_file:
        parser.error("--offline uses built-in example credentials")
    if not args.offline and args.credentials_file is None:
        parser.error(
            "--credentials-file is required for live authentication/collection"
        )

    # This diagnostic is a managed worker, even when deployment config is absent.
    os.environ["OPENHOUND__MANAGED"] = "true"
    os.environ["AWS_EC2_METADATA_DISABLED"] = "true"
    if args.offline:
        access_key, secret = OFFLINE_ACCESS_KEY, OFFLINE_SECRET_KEY
    else:
        supplied = json.loads(args.credentials_file.read_text())
        access_key, secret = supplied["access_key_id"], supplied["secret_access_key"]
    params = (
        json.loads(args.params_file.read_text())
        if args.params_file
        else {
            "sources": {
                "source": {"aws": {"credentials": {"region_name": "us-east-1"}}}
            }
        }
    )
    run(
        local_job(access_key, params),
        secret,
        probe=args.offline or args.auth_only,
        offline=args.offline,
        output=args.output_dir,
    )


if __name__ == "__main__":
    main()
