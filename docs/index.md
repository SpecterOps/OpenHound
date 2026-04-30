# OpenHound

OpenHound is a standardized framework for building OpenGraph collectors and converters. Built
on [DLT](https://dlthub.com/docs/intro) (Data Load Tool), it provides a
consistent workflow for collecting, processing, and converting data from any source into BloodHound-compatible graphs.
OpenHound enforces a collect-first, convert-later pipeline. Raw data collected from a source is always stored before
transformation and ensures reproducibility. Custom decorators simplify collector development with minimal boilerplate,
while CLI commands and graph documentation are automatically generated for every source.

## How it works

```mermaid
flowchart LR


    Collect([Collect]):::step
    Preprocess([Preprocess]):::step
    Convert([Convert]):::step

    Raw[(JSONL/Parquet)]:::storage
    DuckDB[(DuckDB)]:::optional

    OpenGraph[OpenGraph Files]:::output
    BloodHound[BloodHound API]:::output

    Collect --> Raw
    Raw --> Preprocess
    Preprocess -.-> DuckDB
    DuckDB -.-> Convert
    Preprocess --> Convert
    Convert --> OpenGraph

```

**Collect**:
OpenHound uses DLT to collect resources from various services. Resources are parsed using a Pydantic model and stored as
JSONL/Parquet on disk during the collection phase.

**Pre-process**:
A DuckDB database can be (optionally) populated to store resources for OpenGraph conversion. The database can be used as
a lookup to find, for example, all resources a particular user/group has permissions to.

**Convert**:
The raw resources are read from disk and converted to OpenGraph nodes and edges. The generated local OpenGraph JSON
files are automatically split into multiple files based on your configured (entry)
size limit and resource type.

# DLT (Data Load Tool)

DLT Is an open-source Python library to load data from various data sources into well-structured datasets. The dlt
library solves a lot of the issues faced when building a custom data collector for BloodHound. DLT includes features
like:

- Schema validation: Automatically catch potential data formatting issues from your source and before exporting your
  graph;
- Incremental loading: Only process what has changed since your last run;
- Pre-built connectors: Already contains pre-built connectors and an easy to use (generic) HTTP connector which deals
  with pagination automatically 💫;
- Multi-processing: Parallelize resource collection;
- Config management: Simple configuration management for your custom sources, which are read from both environment
  variables and/or centralized config files;

## Supported sources (current state)

- Okta
- Github
- Jamf

## Supported destinations

- File Export: Generates local OpenGraph JSON files which are automatically split into multiple files based on your
  configured (entry) size limit and resource type.
- Ingest API: Generates the same OpenGraph format but uploads the content via the BloodHound BHE API without the storing
  files on disk first. This feature is only supported for BloodHound enterprise customers.
