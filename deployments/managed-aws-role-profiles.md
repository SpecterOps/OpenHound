# Managed AWS role profiles

Managed AWS jobs can use a supplied IAM user's Access Key ID and Secret Access
Key to assume an initial collection role. Configure that role through the
existing BHE collection plan's Configuration JSON (`params`); supply the source
user's keys through the existing credential inputs.

The OpenHound worker creates a private source credentials profile and a linked
role profile, then injects the role profile's generated name into the AWS
extension's configuration. Boto3 performs STS `AssumeRole` and refreshes the
initial role credentials as needed. No AWS extension or BHE changes are required.

OpenHound transports configuration and mechanically adapts the raw-key credential
handoff. The installed extension, DLT, boto3, and AWS determine field meanings,
model selection, required combinations, and validity. OpenHound requires a
non-empty `role_arn` when any initial role setting is supplied, but does not enforce
AWS key formats, role/session/external-ID syntax, organization combinations, or
an allowlist of credential settings.

## Configuration layout and resolution

Configuration JSON uses nested objects corresponding to DLT/TOML sections:

```json
{
  "sources": {
    "source": {
      "aws": {
        "credentials": {
          "region_name": "us-east-1",
          "future_credential_option": {"values": [null, {}, [], false, 0]}
        },
        "future_extension_option": {"enabled": false, "limit": 0}
      }
    }
  }
}
```

Unknown settings, nested mappings/lists, `null`, empty collections, `false`, and
`0` are transported without filtering. Top-level sections must be objects; flat
dotted keys or environment-variable-style keys do not form a DLT document.
Other nested DLT layouts and pipeline-scoped settings are also preserved. DLT
determines precedence and coercion; OpenHound does not relocate supplied settings.

For the raw-key path, the authoritative **claimed job** supplies `params`, the
Access Key ID, and the secret reference. OpenHound retrieves the text secret and
creates isolated AWS files, then installs a copied DLT document **before collector
import or resolution**. Only `sources.source.aws.credentials.profile_name` is
replaced with the generated runtime name. Inputs retained by the scheduler are
not mutated. Arbitrary JSON goes to DLT, not to AWS INI files.

The bundled extension at `b5a5041367fa603c9b159479a3aa327fa3f0059f` resolves an
organization/multi-profile/single-profile DLT credential union. Observed behavior:

- Incomplete organization settings can fall back to single-profile credentials.
- Unknown fields can be ignored, without a downstream error.
- Supplied `profile_names` can select multi-profile credentials even though the
  generated `profile_name` is present. The generated profile may then be unused.

OpenHound preserves these outcomes. It does not reject or compensate for them.
Deployment source-credential defaults are masked in fallback providers for
raw-key jobs; job-supplied settings remain intact. Unsectioned fallback providers
are excluded because they cannot distinguish source credentials from defaults
for other sections. This isolation does not force a particular extension model
or override job-supplied pipeline settings. No session token is added to BHE's
raw-key contract.

## Single-account collection

Use the structure in [managed-aws-role-params.json](managed-aws-role-params.json),
replacing the example role ARN with the actual collection role:

```json
{
  "sources": {
    "source": {
      "aws": {
        "credentials": {
          "region_name": "us-east-1",
          "role_arn": "arn:aws:iam::123456789012:role/OpenHoundCollectorRole",
          "role_session_name": "product-openhound-collection"
        },
        "bucket_policy": {
          "include": false
        }
      }
    }
  }
}
```

| Role setting | Translation into the generated boto3 profile | Behavior |
| --- | --- | --- |
| `role_arn` | Copied when present | boto3/AWS interpret the role identifier. |
| `role_session_name` | Copied when present | Defaults to the generated job profile name when a linked profile is created. |
| `external_id` | Copied when present | boto3 passes it to the initial STS `AssumeRole` request. |

The bridge translates these three fields from
`sources.source.aws.credentials`, retaining them in the DLT document as well.
The current extension does not consume them directly in its credential models.
When any is present, `role_arn` must be a non-empty string; incomplete settings
are rejected during job preparation to prevent fallback to the worker's identity.
The worker writes the raw keys to a generated source profile and adds
`source_profile` wiring to the generated collection profile. Values are otherwise
copied mechanically, without checking AWS syntax. Scalar values must be losslessly
representable in INI; serialization failures produce a sanitized operational error.
Omitting all three preserves direct-key collection. Failed role assumption does
not retry with the source user's permissions.

Supplied profile/authentication settings are preserved, except the injected
canonical `profile_name`. For the generated-profile examples, omit `profile_names`
to let the extension select that profile. The worker owns generated names and
file locations. Files are mode 0600, the workspace is mode 0700, and environment
and DLT providers are restored after collection. Cleanup also covers submission
failure and forced worker termination. An `AWS_ROLE_ARN` environment variable is
not a substitute for the bridge's job setting.

## Organization collection after the initial role assumption

Existing organization settings can be combined with the new role settings.
For source-account organization collection, the resulting credential section
looks like:

```json
{
  "region_name": "us-east-1",
  "role_arn": "arn:aws:iam::123456789012:role/OpenHoundCollectorRole",
  "role_session_name": "product-openhound-collection",
  "source_account": "123456789012",
  "management_account_id": "111111111111",
  "management_role_name": "ManagementCollectorRole",
  "org_role_name": "MemberCollectorRole",
  "org_role_session_name": "product-openhound-member-collection",
  "include_management_account": true
}
```

`source_account` must match the **initial assumed role's account**, not necessarily
the raw-key user's account. The source user must be allowed to assume the initial
role, and the management/member roles must permit assumption from that initial
role. The extension assumes management and member roles directly from the initial
role; member assumptions do not chain through the management role.

For management-account organization collection, set `role_arn` to the management
collection role, retain `org_role_name` and any other applicable organization
settings, and omit `source_account`, `management_account_id`, and
`management_role_name`.

The existing extension constructs member role ARNs using one common
`org_role_name` across member accounts. It does not consume per-account role-name
mappings. Its multi-profile model selects supplied profile names, which may not
exist in the job's isolated generated files. Account IDs
and role names must agree with the actual environment. For IAM permissions and
trust setup, see [Configure AWS Permissions](https://bloodhound.specterops.io/openhound/collectors/aws/configure-permissions).

BHE snapshots parameters when jobs are queued. Update the plan's full JSON
configuration and enqueue a new job to use the role settings.

## Worker logging destinations

Scheduler workers forward OpenHound, extension, DLT, and failure-diagnostic
records through a spawn-context multiprocessing queue to a parent listener.
The parent owns rotating files and delivers records to the configured main
OpenHound handlers, including handlers added only to the parent. DLT records also
reach `ext_<extension>.log` when selected by a pipeline stage; each destination
receives a record once. Worker logging setup/reconfiguration retains forwarding.

Main destinations follow the existing runtime mode and `runtime.log_*` settings:
`openhound.log` under `runtime.log_path` (or the platform default), stdout in
container mode, and the configured Rich console output in CLI mode. Managed
mode defaults to JSON. Logger names, severity, structured extras, and formatted
exception diagnostics survive forwarding. Worker records gain `job_id` and
`collector_extension` unless explicitly provided. Known credential values are
redacted before crossing the process boundary; existing AWS SDK logging controls
remain active, and BHE receives sanitized failure summaries.

The manager-backed queue avoids worker feeder/shared-write-lock corruption on
abrupt termination. Pool replacements reuse the listener; shutdown waits for
workers, drains records, stops the listener, and shuts down the queue manager.
Records interrupted before queue submission completes can be lost on process
death. Standalone CLI and direct calls retain their local logging handlers.

## Local verification with the bundle image

The local harness in `scripts/check_managed_aws.py` now recognizes `assume-role`
credentials and prints the STS caller ARN. `--auth-only` verifies the selected
initial role, not downstream organization-role permissions or service access.

An existing bundle image contains the old adapter. The commands below mount the
updated adapter and harness from this checkout, so an image rebuild is not
needed for this local check. Rebuild the image for a deployed scheduler.

### Offline role check

This uses example source keys and stubbed STS responses with networking disabled:

```sh
docker run --rm --network none --platform linux/arm64 --entrypoint python \
  --mount "type=bind,src=$PWD/src,dst=/app/src,readonly" \
  --mount "type=bind,src=$PWD/scripts/check_managed_aws.py,dst=/app/check_managed_aws.py,readonly" \
  --mount "type=bind,src=$PWD/deployments/managed-aws-role-params.json,dst=/app/role-params.json,readonly" \
  openhound-aws-bundle:arm64 /app/check_managed_aws.py \
  --offline --params-file /app/role-params.json
```

Expected output includes `using assume-role` and an
`arn:aws:sts::...:assumed-role/...` caller ARN. This verifies profile wiring, not
live role permissions.

### Live initial-role authentication

Edit the example parameters file to use the intended role and optional
organization settings. Use the source-key JSON described in
[the bundle's local check instructions](aws-extension-bundle.md#local-managed-profile-and-collection-check).

```sh
docker run --rm -i --platform linux/arm64 --entrypoint python \
  --mount "type=bind,src=$PWD/src,dst=/app/src,readonly" \
  --mount "type=bind,src=$PWD/scripts/check_managed_aws.py,dst=/app/check_managed_aws.py,readonly" \
  --mount "type=bind,src=$PWD/deployments/managed-aws-role-params.json,dst=/app/role-params.json,readonly" \
  openhound-aws-bundle:arm64 /app/check_managed_aws.py \
  --auth-only --credentials-file /dev/stdin --params-file /app/role-params.json \
  < "$HOME/.config/openhound-aws-local/credentials.json"
```

Confirm the caller ARN identifies the requested assumed role. For full collection,
use the bundle's existing collection command with these updated adapter, script,
and parameter mounts, and omit `--auth-only`. For AMD64, change `linux/arm64` and
`:arm64` to the AMD64 equivalents.
