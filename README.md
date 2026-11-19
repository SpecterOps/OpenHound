<p align="center">
  <a href="https://specterops.io" target="_blank">
    <img alt="A project powered by SpecterOps - Creators of BloodHound" src="https://raw.githubusercontent.com/SpecterOps/OpenHound/refs/heads/main/.github/GitHub-Header.png" width="100%" style="max-width: 100%;">
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

## Conversion recovery

If a DLT normalization worker pool breaks during conversion and its failed
package is still pending, OpenHound attempts one fallback using a single
normalization worker. It resumes the saved DLT packages and loads them using
the original destination, without collecting or extracting source data again.
The fallback temporarily overrides normalization worker settings for that
pipeline, including environment and TOML configuration, and restores them before
loading. Collection, preprocessing, and unrelated conversion errors keep their
existing behavior.

Logs identify the failed pipeline and package, the recovery worker count, and
the pipeline working directory. If the fallback fails, the exception propagates
and OpenHound does not explicitly delete pending DLT packages. Preserve the
pipeline directory, collected resource files, and lookup database before starting
another job or changing containers. DLT may have completed some packages before
the failure; recovery is not an atomic rollback of uploads.

A broken worker pool can result from a container memory limit or another abrupt
worker exit. Single-worker normalization reduces concurrency but does not
guarantee that the workload fits in memory. It cannot recover automatically if
the job process or container itself is killed.

The automatic fallback operates within the active job. The shared
`openhound.core.recovery.resume_pending_pipeline` function accepts an already
configured DLT pipeline and resumes pending normalization/loading without
extraction. Callers must exclusively own its state and coordinate the BHE job
lifecycle; it must not run alongside the scheduler on the same pipeline.

### Saved runs and recovery after a restart

Enable saved runs for future scheduler jobs in `.dlt/config.toml`:

```toml
[openhound]
runs_directory = "/var/lib/openhound/runs"
```

The equivalent environment variable is `OPENHOUND__RUNS_DIRECTORY`. For persistence
across container replacement, mount this directory on persistent storage and make
it writable by the container user.
Configuring a directory alone does not make a container's filesystem persistent.
Without this setting, a local Python scheduler keeps its existing storage paths.
Docker images enable saved runs at `/var/lib/openhound/runs` by default, using the
container filesystem. This image default applies only when `runs_directory` is
unset; explicit TOML settings and `OPENHOUND__RUNS_DIRECTORY` take precedence.
A volume is optional; see the
[Docker storage configuration](example-configurations/bloodhound-enterprise/README.md#saved-runs-inside-the-container)
for persistent storage and package retrieval.

For Kubernetes, the [Helm chart](deployments/helm/openhound/README.md#persistent-saved-runs)
can mount an existing PersistentVolumeClaim for saved runs.

Each job gets a new run ID, logged with its directory. The workspace contains:

```text
<runs_directory>/<run-id>/
  run.json             # Job association, versions, and phase checkpoints
  collection-inventory.json  # Relative paths and sizes of collected JSONL files
  raw/                 # Collected resource files
  lookup.duckdb        # Original preprocessing database, when applicable
  pipelines/           # Original DLT state and packages
  graph/               # Managed job staging; removed by normal upload cleanup
  recovery/            # Separate local recovery pipelines, lookup, and graph files
```

After collection completes, OpenHound records the collected `.jsonl.gz` file
paths and byte sizes in `collection-inventory.json`. Paths are relative to the
workspace. The inventory is informational: recovery and packaging do not read or
validate it, and older runs do not need one. File contents are not read or hashed
to create it. Inventory write failures are logged and do not fail the run.

Inspect a saved run or recover its graph files:

```sh
openhound runs inspect /var/lib/openhound/runs/<run-id>
openhound runs resume /var/lib/openhound/runs/<run-id> --workers 1
```

For a distroless scheduler container, invoke Python directly:

```sh
docker exec <container> python -m openhound runs inspect /var/lib/openhound/runs/<run-id>
docker exec <container> python -m openhound runs resume /var/lib/openhound/runs/<run-id> --workers 1
```

The commands refuse concurrent access to a run. Collection must have completed:
recovery never repeats API collection. It reuses a completed lookup database or
rebuilds preprocessing locally when necessary. The first recovery generates graph
records from saved resources into a separate file-output pipeline. This leaves
the original ingest packages intact. Later attempts resume recovery packages
without regenerating graph records when pending packages exist. A completed
recovery returns its existing output directory rather than generating duplicates.

Recovered files are written under `recovery/graph`. **The resume command does not
upload to BHE or reopen a failed BHE job.** A manifest's `complete` status refers
to local pipeline phases, not confirmation that BHE finished the job. Uploading
recovered files requires a separately coordinated ingest/job workflow.

Create a ZIP for a BloodHound user to upload after recovery completes:

```sh
openhound runs package /var/lib/openhound/runs/<run-id> --output recovered-okta.zip
```

The command creates `recovered-okta.zip` and
`recovered-okta.zip.manifest.json`. Upload **only the ZIP** through BloodHound's
file upload workflow, using a user with ingestion permissions. Keep the manifest
locally for its run/job provenance, node/edge counts, and SHA-256 checksums.
This is a separate manual ingestion; it does not reopen or change the original
collector job, and packaging makes no requests to BloodHound.

Packaging locks the run, requires completed recovery, and validates each graph
part against OpenHound's graph model. It includes only files listed in the graph
destination's completion markers, with their original bytes unchanged. Missing
parts, invalid graph documents, and empty graphs are rejected. Raw resources,
DuckDB, DLT state, staging files, and the audit manifest are excluded from the
ZIP. Existing outputs are never overwritten; choose another output name to
create another package. The output parent directory must exist and support
hard links, which are used to publish completed artifacts without overwriting
files. Interrupted builds can leave a hidden temporary directory; they do not
publish a partially written ZIP.

Use the same OpenHound, DLT, and collector versions, plus the same tenant/source
configuration needed by the collector's conversion hook. No configuration or
credentials are copied into `run.json`. Recovery can run in a larger container:
copy the entire idle workspace, not just DuckDB or JSONL files, and pass its new
directory to the commands above. Unlocked `running` checkpoints may represent
an interrupted process; inspection does not infer success from that status.

Saved workspaces are retained without automatic pruning, including successful
runs. Plan disk capacity and remove runs only after their data is no longer
needed and no process owns them. This feature does not import data collected
before saved runs were enabled.

## Available extensions

Extend OpenHound with pre-built extensions for other services. See the
[installation instructions](https://bloodhound.specterops.io/openhound/community#install-the-openhound-cli)
for installing additional collectors.

| Name   | Source repo                                    |
|--------|------------------------------------------------|
| Github | https://github.com/SpecterOps/openhound-github |
| JAMF   | https://github.com/SpecterOps/openhound-jamf   |
| Okta   | https://github.com/SpecterOps/openhound-okta   |
