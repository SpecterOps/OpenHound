
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

The equivalent environment variable is `OPENHOUND__RUNS_DIRECTORY`. Mount this
directory on persistent storage and make it writable by the container user.
Configuring a directory alone does not make a container's filesystem persistent.
Without this setting, the scheduler keeps its existing storage paths.

Each job gets a new run ID, logged with its directory. The workspace contains:

```text
<runs_directory>/<run-id>/
  run.json             # Job association, versions, and phase checkpoints
  raw/                 # Collected resource files
  lookup.duckdb        # Original preprocessing database, when applicable
  pipelines/           # Original DLT state and packages
  graph/               # Managed job staging; removed by normal upload cleanup
  recovery/            # Separate local recovery pipelines, lookup, and graph files
```

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

