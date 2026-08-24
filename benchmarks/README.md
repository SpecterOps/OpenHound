# OpenGraph batching benchmark

`opengraph_batching.py` is a manual tool for comparing batching behavior. It is
separate from pytest; generated files go to git-ignored `.benchmark-results/`.

Run commands from the repository root with the project environment:

```powershell
.\.venv\Scripts\python.exe benchmarks\opengraph_batching.py --help
```

Each run creates a timestamped folder with `metrics.json` and generated output.
Use `--output-dir <path>` to change the location.

To measure a real local DLT load, including package-file and callback metrics:

```powershell
.\.venv\Scripts\python.exe benchmarks\opengraph_dlt_pipeline_metrics.py --rows 100000
```

## Modes

### Synthetic: `per-row`

Simulates the old behavior: one conversion per input row.

```powershell
.\.venv\Scripts\python.exe benchmarks\opengraph_batching.py `
  --rows 100000 --edges-per-row 1 --batch-size 150 --mode per-row
```

### Synthetic: `page-batched`

Simulates the new behavior: relationships are combined across DLT-page rows,
then flushed at `--batch-size` and page end.

```powershell
.\.venv\Scripts\python.exe benchmarks\opengraph_batching.py `
  --rows 100000 --edges-per-row 1 --batch-size 150 --mode page-batched
```

Run both modes with the same arguments for a fair comparison. Saved 100k/1m
results are in `opengraph_batching_results.json`.

### Graph replay

Replays final graph JSON from a collection, such as `graph/okta` or
`graph/github`.

```powershell
.\.venv\Scripts\python.exe benchmarks\opengraph_batching.py `
  --graph-dir graph/okta --graph-glob 'applicationuser_fs-*.json' `
  --batch-size 150
```

`--graph-glob` defaults to `*_fs-*.json`. This cannot exactly compare old/new
source batching because final graph files lack raw row/page boundaries.

### Raw DLT JSONL replay

Replays raw DLT JSONL through the real extension model. It can exactly compare
legacy per-row and page-batched output. Supported targets:

- `--raw-source okta --raw-table application_users`
- `--raw-source github --raw-table org_role_members`

Pass `--lookup-file` when the model needs the collection's DuckDB lookup file.

```powershell
.\.venv\Scripts\python.exe benchmarks\opengraph_batching.py `
  --raw-dir output/okta --raw-source okta --raw-table application_users `
  --lookup-file lookup.duckdb --batch-size 150 --compare-source-batching
```

`--compare-source-batching` runs both versions, records hashes, and fails if
they differ. Normal metrics are page-batched;
`legacy_per_row_comparison` contains the baseline.

## Reading `metrics.json`

Key fields: `wrapper_items`, `inner_relationships`, `destination_parts`,
`maximum_relationships_per_part`, `maximum_part_bytes`, timing, peak memory,
and `flattened_semantics` (result hashes).

The tool does not create a real DLT package, so `dlt_package_files` is `null`
and callbacks are simulated. Synthetic runs exclude model and DuckDB work; use
the results as local comparisons, not customer performance claims.
