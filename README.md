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

## Uploading to BloodHound

BloodHound needs two things from a collector: the extension **schema**, which describes the node and
relationship kinds, and the **collection**, which is the data itself. OpenHound can send both.

### Configure where BloodHound is

Put the BloodHound address in `.dlt/config.toml` and the credentials in `.dlt/secrets.toml`, under the
`destination.bloodhound` section. This is the same section the saved-search and privilege-zone uploads use.
Use an API token pair (created under *My Profile → API Key Management* in BloodHound) or a bearer token from
a signed-in session.

```toml
# .dlt/config.toml
[destination.bloodhound]
url = "http://localhost:8080"
```

```toml
# .dlt/secrets.toml, with an API token pair...
[destination.bloodhound]
token_id = "..."
token_key = "..."

# ...or with a bearer token instead
[destination.bloodhound]
token = "..."
```

As environment variables these are `DESTINATION__BLOODHOUND__URL`, `DESTINATION__BLOODHOUND__TOKEN_ID`,
`DESTINATION__BLOODHOUND__TOKEN_KEY`, and `DESTINATION__BLOODHOUND__TOKEN`. The token pair is used when both
halves are present; otherwise the bearer token is used.

When `destination.bloodhound` holds no credentials, the upload commands read the
`destination.bloodhoundenterprise` section, the one Enterprise ingest uses. `convert --method ingest` to
Enterprise reads `destination.bloodhoundenterprise`.

### Install or update a schema

```console
# One schema file, or a zip bundle containing schema.json
openhound schema upload ./schema.json
openhound schema upload ./my-extension-bundle.zip

# Every .json and .zip file in a folder
openhound schema upload ./schemas

# The schema.json that an installed collector ships next to its extension.yaml
openhound schema upload sccm

# Check the files without contacting BloodHound
openhound schema upload ./schemas --dry-run
```

The command prints one line per file (`created`, `updated`, `skipped`, or `failed`) and exits with
status 1 when any file failed. BloodHound matches extensions by the `schema.name` field, so uploading a
changed file with the same name updates the existing extension.

Collectors that want `openhound schema upload <name>` to work should ship their schema as `schema.json`
in the same package folder as `extension.yaml`.

### Upload a collection

After `openhound convert` has written OpenGraph JSON files, send them to BloodHound:

```console
# An OpenHound convert output folder (every .json and .zip directly inside it)
openhound graph upload ./out/graph

# A single OpenGraph JSON file, or a zip of them such as ConfigManBearPig's bundle
openhound graph upload ./out/graph/users-1.json
openhound graph upload ./out/configmanbearpig_collection.zip

# Check the files without contacting BloodHound
openhound graph upload ./out/graph --dry-run

# Return as soon as the files are sent instead of waiting for BloodHound to process them
openhound graph upload ./out/graph --no-wait

# Cut large JSON files into parts of at most 2,000 nodes plus edges (default 5,000; 0 sends each file whole)
openhound graph upload ./out/graph --batch-size 2000

# Send JSON files uncompressed (they are gzip-compressed by default)
openhound graph upload ./out/graph --no-compress
```

The command opens one BloodHound file-upload job, attaches every file to it, and ends the job. It
prints one line per file (`uploaded`, `skipped`, or `failed`) and the job number. By default it then
waits, for up to ten minutes (`--timeout`), until BloodHound reports the job complete and prints the
result. It exits with status 1 when any file failed or the job did not complete. Progress is also
visible under *Administration → File Ingest* in BloodHound.

Each JSON file is checked locally before anything is sent: it must parse and must have a top-level
`graph` section. JSON files are gzip-compressed before sending, which makes large collections much
smaller on the wire; BloodHound unpacks them on arrival. Zip files are already compressed, so they are
sent as they are and checked by BloodHound.

A JSON file with more than `--batch-size` nodes plus edges (default 5,000) is cut into parts that are
all attached to the same job. Each part keeps the file's `metadata` section, and all node parts are sent
before any edge part. Parts are named `<file>.part-0001.json`, `<file>.part-0002.json`, and so on. Zip
files cannot be cut and are always sent whole.

### Convert straight into BloodHound

`openhound convert` can send its output to BloodHound instead of writing files, with `--method ingest`:

```console
openhound convert <collector> ./collected/<collector> ./unused --method ingest
```

The output folder argument is required but not used with `--method ingest`; nothing is written to it.
The default, `--method write`, saves OpenGraph JSON files to the output folder.

OpenHound asks the configured BloodHound which edition it is and sends the data the way that edition
expects:

- **Community Edition**: the settings come from `destination.bloodhound`, or from
  `destination.bloodhoundenterprise` when `destination.bloodhound` holds no credentials (see
  [Configure where BloodHound is](#configure-where-bloodhound-is)). The run opens one BloodHound
  file-upload job, attaches every batch of 1,000 converted items to it as a gzip-compressed OpenGraph
  file, and ends the job. Each file carries the collector's source kind in its `metadata` section, so
  the data can later be removed by source kind under *Administration → Database Management*. The command
  does not wait for BloodHound to process the job; watch it under *Administration → File Ingest*.
  BloodHound runs its analysis once, after the job ends. If the conversion fails part-way, the job is
  ended anyway, so BloodHound processes whatever arrived.
- **Enterprise**: the data goes to `/api/v2/ingest` using the `destination.bloodhoundenterprise`
  settings. OpenHound also takes this route when the edition cannot be determined: neither section
  holds credentials, or BloodHound cannot be reached or does not answer the question.

The Enterprise scheduler service always takes the Enterprise route.

## Available extensions

Extend OpenHound with pre-built extensions for other services. Additional collectors can be installed
using [pip extras](https://didactic-adventure-wrzl3gr.pages.github.io/getting-started/).

| Name   | Source repo                                    |
|--------|------------------------------------------------|
| Github | https://github.com/SpecterOps/openhound-github |
| JAMF   | https://github.com/SpecterOps/openhound-jamf   |
| Okta   | https://github.com/SpecterOps/openhound-okta   |
