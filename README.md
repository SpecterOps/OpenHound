<p align="center">
  <a href="https://specterops.io" target="_blank">
    <img alt="A project powered by SpecterOps - Creators of BloodHound" src=".github/GitHub-Header.png" width="100%" style="max-width: 100%;">
  </a>
</p>

<h4 align="center">
  Build BloodHound data collectors with OpenHound's standardized, reproducible collect-first and transform-later pipeline.
</h4>

<!-- Standard shields, please do not remove -->
<p align="center">
  <a href="https://slack.specterops.io"><img src="https://custom-icon-badges.demolab.com/badge/Slack-BloodHound%20Gang-4A154B?logo=slack&logoColor=fff" alt="Slack"/></a>
  <a href="https://reddit.com/r/SpecterOpsCommunity"><img src="https://img.shields.io/badge/Reddit-r/SpecterOpsCommunity-FF4500?logo=reddit&logoColor=white" alt="SpecterOps on Reddit"/></a>
  <a href="https://github.com/specterops"><img src="https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fspecterops%2F.github%2Fmain%2Fconfig%2Fshield.json&style=flat" alt="Sponsored by SpecterOps"/></a>
</p>


<p align="center">
  <a href="https://x.com/SpecterOps"><img src="https://img.shields.io/twitter/follow/SpecterOps?style=social" alt="@SpecterOps on Twitter"/></a>
  <a href="https://www.linkedin.com/company/specterops/"><img src="https://custom-icon-badges.demolab.com/badge/LinkedIn-0A66C2?logo=linkedin-white&logoColor=fff" alt="Connect on LinkedIn"/></a>
  <a href="https://infosec.exchange/@specterops"><img src="https://img.shields.io/mastodon/follow/109314317500800201?domain=https%3A%2F%2Finfosec.exchange&style=social" alt="Connect on Mastodon"/></a>
</p>

---

## About

OpenHound is a standardized framework for building OpenGraph collectors and converters. Built
on [DLT](https://dlthub.com/docs/intro) (Data Load Tool), it provides a
consistent workflow for collecting, processing, and converting data from any source into BloodHound-compatible graphs.
OpenHound enforces a collect-first, convert-later pipeline. Raw data collected from a source is always stored before
transformation and ensures reproducibility. Custom decorators simplify collector development with minimal boilerplate,
while CLI commands and graph documentation are automatically generated for every source.

[![Python Version](https://img.shields.io/badge/Python-3.13-brightgreen.svg)](#about)
[![Tests](https://github.com/SpecterOps/openhound/workflows/Run%20tests/badge.svg)](https://github.com/SpecterOps/openhound/actions/workflows/tests.yml)

## Getting Started

Follow the docs for setup, CLI usage, and collector development:

- Overview: [OpenHound Documentation](https://bloodhound.specterops.io/openhound/overview)

## How it works

- **Collect**: OpenHound uses DLT to collect resources from various services. Resources are parsed using a Pydantic
  model and stored as JSONL/Parquet on disk during the collection phase.

- **Pre-process**: A DuckDB database can be (optionally) populated to store resources for OpenGraph convertion. The
  database can be used as a lookup to find, for example, all resources a particular user/group has permissions to.

- **Convert**: The raw resources are read from disk and converted to OpenGraph nodes and edges.

## Available extensions

Extend OpenHound with pre-built extensions for other services. Additional collectors can be installed
using [pip extras](https://didactic-adventure-wrzl3gr.pages.github.io/getting-started/).

| Name   | Source repo                                    |
|--------|------------------------------------------------|
| Github | https://github.com/SpecterOps/openhound-github |
| JAMF   | https://github.com/SpecterOps/openhound-jamf   |
| Okta   | https://github.com/SpecterOps/openhound-okta   |

## Managed AWS access-key jobs (beta)

Managed AWS collection requires the AWS extension and BHE's collector queue
contract from bloodhound-enterprise PR #2247 (BED-10229). Queue operations use
`/api/v2/collie-job-queue`. The authoritative `POST /{job_id}/claim` response
contains `data.job` with these required, nullable string fields:

- `external_secret_key_id`: the customer's AWS Access Key ID, snapshotted from
  the BHE secret record's `external_key_id` when the job is queued.
- `secret_key_id`: the Secrets Manager reference for the raw Secret Access Key.

The job field is named **`external_secret_key_id`**, including in available and
current-job responses. BHE must deploy the API and database migration; enqueue
new jobs after rollout so the key identifier is populated. The migration does
not backfill existing queue entries.

This beta accepts long-lived IAM keys (`AKIA` plus 16 uppercase alphanumeric
characters) and a raw, 40-character Secret Access Key. It validates their format
before `/start`; AWS authenticates them during collection. Temporary source keys
(`ASIA`), session-token inputs, JSON-wrapped keys, whitespace, and conflicting
profile/authentication selections are rejected. Credentials are never trimmed or
rewritten. Secrets Manager's text and UTF-8 binary representations are supported.

Keep collection settings in the job's nested `params`, for example:

```json
{
  "sources": {
    "source": {
      "aws": {
        "credentials": {
          "region_name": "us-east-1",
          "source_account": "999999999999",
          "management_account_id": "111111111111",
          "management_role_name": "ManagementCollectorRole",
          "org_role_name": "OpenHoundCollectorRole"
        },
        "bucket_policy": {"include": false}
      }
    }
  }
}
```

For a single account, omit the organization account/role settings. Endpoint,
region, organization, and collection settings are preserved. The worker supplies
its generated name at `sources.source.aws.credentials.profile_name` using the
job-scoped DLT provider. Deployment source-credential defaults cannot change the
selected authentication model. The existing nested-document secret flow remains
available when `external_secret_key_id` is null; arbitrary string secrets only
enter the adapter for AWS jobs.

### Credential workspace lifecycle

Only the collection worker writes credentials and sets `AWS_SHARED_CREDENTIALS_FILE`
and `AWS_CONFIG_FILE`. Its context restores environment and DLT providers and
removes the files on success or failure. The scheduler retains its own AWS
identity for Secrets Manager. Worker logs redact the pair, and SDK response logs
are suppressed so STS credentials are not logged.

The scheduler owns a private job directory beneath
`<system-temp>/openhound-aws-profiles-<uid>/` (directories `0700`, files `0600`),
separate from collection output and support bundles. It removes the directory
when processing the worker's completion, including abrupt death, or after
killing and joining a worker on claim loss. Credentials are removed before
upload/end retries.

Parent and worker OS file locks protect live workspaces. On managed scheduler
startup and before each new AWS workspace, abandoned directories are reclaimed
only when both locks are available. An orphaned live worker retains its files
until it exits and a subsequent sweep reclaims them. For container deployments,
use a private ephemeral temporary volume (for example a memory-backed `TMPDIR`);
volume destruction removes material if the entire container terminates. On a
persistent temporary volume, the next scheduler startup performs reclamation.

### Adapter verification

Run the focused tests with the actual AWS extension checked out alongside
OpenHound, installed, or selected with `OPENHOUND_AWS_SOURCE`:

```shell
OPENHOUND_AWS_SOURCE=/path/to/openhound-aws uv run pytest \
  tests/test_aws_profile_adapter.py tests/test_bhe_job_scheduling.py \
  tests/test_managed_configuration.py
```

The real-model integration tests load the extension's authentication module
without its unrelated native policy engine; they are skipped when the internal
extension is unavailable. SDK file-provider, worker lifecycle, and redaction
tests run independently. AWS requests in these tests are stubbed.
