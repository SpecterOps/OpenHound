## Quick-start

   1. Copy `.dlt-example` to `${HOME}/.dlt`. From the repository root, run `cd example-configurations/bloodhound-enterprise` before the Compose commands below so the configured build context can access the root `Dockerfile`.
   2. Fill in your credentials in the toml files.
   3. Place any required key files (github.pem, okta.json) in `${HOME}/.dlt`.
   4. Pull image from SpecterOps Docker Hub:  `docker pull specterops/openhound:latest-enterprise`
      or run to pull from docker-compose.yml: `docker compose pull`
   5. Run all collectors:    `docker compose up -d`
      or run a single one:   `docker compose up -d scheduler-jamf`

Full configuration reference: https://bloodhound.specterops.io/openhound/enterprise

## WARNING: 
 All config and secret files referenced below MUST exist before running
 `docker compose up`. If they are missing, Docker will create them as directories,
 which will cause the collector to fail.

## Managed AWS compatibility: organization survey

The OpenHound-only `poc/managed-aws-dlt-credentials` branch supports the updated
AWS extension using its existing SDK environment authentication. The extension
is unmodified. The profile adapter, file generation, profile injection,
authentication preflights and workspace cleanup have been removed.

### Job params: configuration only

Use [managed-aws-job-params.json](managed-aws-job-params.json) as BHE's `params`
document. It uses the extension's supported DLT field names and contains no secret
values. Under `sources.source.aws.credentials`, the survey requires:

- `management_account_id`: organization management account.
- `collector_assumed_role_name`: member role; also the default management role
  when a management assumption is needed.
- `member_role_source`: `source` for direct member assumptions from the supplied
  identity, or `management` to chain through the management identity.

The example names `management_role_name` explicitly so IAM user keys from the
management account can also take a management-role hop. The extension performs
that assumption automatically. The example sets `runtime_auth_mode = "default_chain"`
for the SDK environment path and the optional source setting `max_attempts`.
The two role-name fields may name the same existing role; separate management
and member role names are supported but not required.

Survey is selected by **omitting `aws_config_file`**, not by enabling a generation
flag. `auto_generate_config` was removed and is rejected. Leave `survey_only`
unset/false to survey and collect; true means generate the file and stop.

| Old setting | Current setting |
| --- | --- |
| `profile_name` | Omit for SDK environment credentials. `collector_profile_name` selects an existing file-based starting profile. |
| `org_role_name` | `collector_assumed_role_name` |
| `org_role_session_name` | `collector_assumed_role_session_name` |
| `source_account` | Select the starting identity through credentials; configure `member_role_source` explicitly. |
| `region_name` / `include_management_account` | Removed; the extension collects enabled regions and includes management. |

OpenHound forwards configuration unchanged; it does not translate these fields
or validate AWS collection semantics. The extension reports invalid settings.

### Migrating the supplied job example

Two complete examples preserve the supplied account ID, role names, S3 rules,
session label and 60-second collection read timeout:

- [Direct member assumptions](managed-aws-source-members.json): use when member
  roles trust the identity of the stored keys. The management role is used to
  survey the organization and collect management; member roles use the starting
  key identity directly.
- [Management-chained member assumptions](managed-aws-management-members.json):
  use when member roles trust the management identity. The starting keys assume
  `kng-openhound-organization-role` in `594194277364`, then that role assumes
  `OpenHoundCollectorRole` in each member account.

Both examples select survey by omitting `aws_config_file`. No `survey: true` or
`auto_generate_config` switch is needed. The flow is:

```text
BHE job JSON -> job-scoped DLT settings -> extension surveys organization
             -> extension writes AWS role-routing config -> collection
```

DLT reads the JSON settings; the AWS extension writes the AWS config. Customer
keys are delivered separately in the worker environment. Named roles must already
exist with the corresponding permissions and trust relationships.

| Supplied field | Migration |
| --- | --- |
| `management_account_id` / `management_role_name` | Keep the supplied values. |
| `org_role_name` | Rename to `collector_assumed_role_name`. |
| `role_session_name` | Use `collector_assumed_role_session_name` for the management/member assumptions. This is a shared label, rather than an initial-profile-only label. |
| `source_account` | Remove. The SDK discovers the starting account from the supplied credentials. Choose `member_role_source` explicitly. |
| `include_management_account` | Remove. Management is included by the extension. |
| `region_name` | Remove. The extension collects every enabled region; this is not a replacement region filter. |
| `validate_authentication` | Remove. It is not a current source setting; the extension authenticates collection profiles up front. |
| `role_arn` | Remove the adapter-only starting-role setting. The examples use role names supported by survey. |
| `bucket_policy` / `read_timeout` | Preserve unchanged. |

The previous adapter-only starting role
`arn:aws:iam::604472166808:role/kng-openhound-collection-role` is not recreated by
these examples. They start from the identity of the stored keys. The member role
remains `OpenHoundCollectorRole`, matching the old `org_role_name`. If the actual
role deployed in the member accounts has a different name, use that name for
`collector_assumed_role_name`; survey uses one member-role name throughout the
organization.

The all-match bucket/object expressions are preserved. Object inventory is still
bounded by `bucket_policy.max_objects`, whose default is 10 per bucket. Matching
all objects does not make that bound unlimited.

For these examples, the extension generates a management profile with:

```ini
[profile openhound-management]
role_arn = arn:aws:iam::594194277364:role/kng-openhound-organization-role
role_session_name = product-openhound-collection
credential_source = Environment
bloodhound_collect = true
bloodhound_organization_management = true
```

For a discovered member account, the extension writes the member's own account ID
and `OpenHoundCollectorRole`. Direct mode uses `credential_source = Environment`;
management-chained mode uses `source_profile = openhound-management`. Neither file
contains the supplied keys. These are generated internal directives, not settings
the customer has to put in their JSON.

### Credentials: worker environment only

The existing BHE retrieval contract is preserved:

1. `external_secret_key_id` supplies the access key ID.
2. The scheduler retrieves the text secret access key from Secrets Manager using
   `secret_key_id`, with its own AWS identity. UTF-8 `SecretBinary` is supported.
3. Only the collection worker receives `AWS_ACCESS_KEY_ID` and
   `AWS_SECRET_ACCESS_KEY` through a job-scoped environment. Inherited profile,
   web-identity and session-token selectors are cleared, and default AWS file
   paths point at the null device for this raw-key job.
4. Survey options still reach the extension through the existing
   `managed_job_configuration(params, secrets)` DLT provider. Retrieved nested
   secret documents remain supported when `external_secret_key_id` is null.

**Do not put secret values in job-params JSON.** Raw keys are SDK environment
credentials, not DLT credential fields. No new JSON/TOML/credentials file stores
them. The extension generates its own account/role routing config with
`credential_source = Environment`; collection and subsequent role assumptions
continue using the worker's supplied identity. The current split-key contract
contains no session token, so an inherited token is removed rather than attached
to the customer's key pair.

### Scope and limitations

With JSON configuration and separately stored customer keys, but no customer AWS
files, the beta supports these collection setups:

| Setup | Supported without customer AWS files? | Management-role behavior |
| --- | --- | --- |
| Starting keys are outside management | Yes | Automatically assumes `management_role_name`, or `collector_assumed_role_name` when omitted. |
| Starting IAM user keys are in management, with `management_role_name` | Yes | Automatically assumes the named role in the same account. |
| Member roles trust the starting identity | Yes | Use `member_role_source = "source"`; members are assumed directly from the starting keys. |
| Member roles trust the management identity | Yes | Use `member_role_source = "management"`; members are assumed through management. |
| Environment-based management identity with no role hop | No | The extension reuses the starting identity, then cannot represent it in the generated file. |
| Standalone account, arbitrary independent account list, mixed per-account role names/trust paths | No for this JSON-only beta | These use the extension's finished-config strategy, requiring an existing `aws_config_file`. |
| Generate the config and stop (`survey_only = true`) | Not a managed collection mode | The CLI stops before collection; the managed worker currently reports that stop as a failed job. |

The survey always discovers the organization's active accounts and generates
the routing file itself. Named IAM roles and their trust policies must already
exist; survey generates AWS configuration, not IAM roles. It does not guess the
member trust path or provide a JSON-only account allowlist.

- This beta path demonstrates organization survey followed by collection, with
  either member-role path. It requires an actual management-role hop. Environment
  credentials already in management with no role hop are unsupported by the
  current extension. When starting in management, `management_role_name` must
  select a role the starting identity will actually assume; already being that
  role can still trigger the no-hop limitation.
- For this path, leave `collector_profile_name`, `aws_config_file`,
  `source_config_file`, `credentials_file` and `survey_only` unset. This is not a
  demonstration of finished-config or independent-account/profile collection.
- Missing/blank key inputs fail preparation. Invalid keys, invalid configuration,
  management authentication failure, or no authenticated collection profile fail
  the managed job. Denied members and optional resource reads retain the
  extension's best-effort behavior and can produce partial success.
- Environment values are scoped to a single worker job and restored on exit.
  The scheduler retains its Secrets Manager identity. No starting-credential
  refresh is added, and a generated routing file needs the credentials again
  when reused in another process.
- Each collection job surveys again when `aws_config_file` is omitted, even if a
  previous job left a generated file. A successful survey backs up the old file
  and replaces it with the current job's routing configuration. Survey failure
  fails the job rather than falling back to the previous file. Conversion within
  that same job reads the newly generated file. Backups currently accumulate;
  job-scoped artifact cleanup and isolation between concurrent workers require
  an extension lifecycle contract.

Logging ownership is unchanged for this fix: OpenHound retains redaction,
disabled DLT value tracing, safe SDK diagnostics and boto3/botocore protections
in `scheduler/credentials.py`. Credential retrieval and validation use the same
preparation path for every extension. A temporary binding table maps BHE's
identifier/secret inputs to the current AWS SDK environment names and isolation
settings only for `job_key = "aws"`; numeric job type IDs do not select that
binding. The binding's TODO tracks replacement by the generic credential handoff
and extension-owned SDK environment handling. Authentication, role assumptions, discovery
and collection belong to the extension. Discovery uses its standard entry point
without a hard-coded AWS source import.

### Verification

OpenHound's tests cover its configuration precedence, secret retrieval,
credential/environment handoff, worker error propagation, logging and managed
job lifecycle. Shared configuration tests use a synthetic source and do not
require the AWS extension. Run them from OpenHound:

```bash
python -m pytest tests/test_managed_configuration.py tests/test_managed_credentials.py tests/test_bhe_job_scheduling.py tests/test_log_handlers.py tests/test_aws_secrets_manager.py tests/test_bhe_credentials.py -q
```

AWS authentication, survey, role routing, generated configuration and collection
semantics are owned by the extension and should be verified in its test suite.
