# BED-9372 destination memory & part-size review

Acceptance criteria: peak memory and part sizes stay bounded independently of
table cardinality, against the destination's 1,000-item batch, without
unsupported upload artifacts.

## Revision notes

- An earlier revision called peak RSS "orthogonal to the batching fix." Wrong:
  growth was linear (~1.2 KB per relationship) because each wrapper now carries
  up to `batch_size` relationships. Root cause and fix below.
- Causal chain: the batching fix is the feature; the memory fix was required to
  keep it viable at scale (RSS scaled linearly with cardinality); and the memory
  fix exposed a latent DLT 1.26.0 data-corruption defect, which required an
  in-process correction — without it, delivery silently duplicates items.
- The first mitigation attempt (buffer=333, DLT untouched) demonstrated that
  defect: the faker BHE scheduler test ingested 2,875 nodes where 1,000 were
  expected. Corrected in-process; all numbers below re-measured after.

## Root cause

DLT's writer buffer flushes on **item count** (`data_writer.buffer_max_items`,
default 5,000), and each edge wrapper counts as one item regardless of its
content. Post-batching that is up to

    5,000 items x 150 edges = 750,000 relationships in RAM per flush.

Measured: peak RSS 255 MB at 100k rows -> 1,257 MB at 1M rows (~1.2 KB per
relationship); the `batch_size=1` baseline stayed flat at ~130 MB.

## DLT 1.26.0 jsonl batching defect (corrected in-process)

`DestinationJsonlLoadJob.get_batches` (`dlt/destinations/job_impl.py:240`)
yields the accumulated batch at the end of every load-file line without
resetting it, so multi-line files re-deliver earlier items as a growing prefix:

    buffer=333, 1,200 rows -> 1,208 wrappers delivered as
    333 + 666 + 999 + 1000 + 208 = 3,206 items

Stock settings are only accidentally safe: 5,000 divides evenly by the
destinations' `batch_size=1000`, so partial batches stay empty until each
file's last line. Any other buffer value triggers duplicates. Upstream
refactored this code in release 1.27.0 (`JsonlFileBatchIterator`, verified)
with correct semantics; `openhound.core.dlt_jsonl_batching` installs those
semantics process-wide for dlt 1.26.x, making any buffer value safe.

## Shipped mitigation

`writer_buffer_max_items()` scales the buffer so buffered *relationships* stay
near a fixed budget; applied via `DATA_WRITER__BUFFER_MAX_ITEMS` (`setdefault`,
user overrides win) in `Converter.pipeline` and the benchmark harness:

    buffer_max_items = min(5000, max(1, 50_000 // batch_size))   # 333 @ 150

More frequent flushes write to the same open file; wall time is unchanged
within noise. The jsonl batching module above is what makes 333 safe:
`writer_buffer_max_items()` only tunes when the correction is active, and
otherwise returns the stock default (5,000 aligns with the destinations'
batch size) — degraded memory, never duplication.

## Method

`benchmarks/opengraph_batching_benchmark.py`, one-edge shape, 4 files, DLT
1.26.0 with the batching module active, single load worker. Table-wide =
`batch_size=150`; baseline = pre-fix `batch_size=1`. Untuned rows set
`DATA_WRITER__BUFFER_MAX_ITEMS=5000` explicitly (delivery identical with and
without the correction, since 5,000 aligns with the destination batch size).

## Results

Peak RSS (sampled at 50 ms):

| Scale | Mode | buffer | Wrappers | Peak RSS | Wall |
|------:|------|-------:|---------:|---------:|-----:|
| 100k | baseline | 5,000 | 100,000 | 130 MB | 18.2s |
| 100k | table-wide untuned | 5,000 | 667 | 255 MB | 6.5s |
| 100k | table-wide tuned | 333 | 667 | 240 MB | 6.6s |
| 1M | baseline | 5,000 | 1,000,000 | 132 MB | 111.8s |
| 1M | table-wide untuned | 5,000 | 6,667 | 1,257 MB | 36.2s |
| 1M | table-wide tuned | 333 | 6,667 | 499 MB | 38.9s |
| 4M | table-wide tuned | 333 | 26,667 | 498 MB | 161.5s |

Tuned RSS is **flat across cardinality** (499 MB @ 1M vs 498 MB @ 4M); the
untuned trend extrapolates to multiple GB at customer scale (12.5M+ rows). The
benchmark reports `peak_rss_per_edge` and enforces a guard band (300 MiB floor
+ 512 B/edge) — fires on untuned runs, silent on tuned ones.

Destination callbacks / parts (one JSON part per callback):

| Scale | Mode | Callbacks / Parts | Max rel/callback | Max bytes/callback |
|------:|------|------------------:|-----------------:|-------------------:|
| 100k | baseline | 100 / 100 | 1,000 | 178 KB |
| 1M | baseline | 1,000 / 1,000 | 1,000 | 180 KB |
| 1M | table-wide untuned | 7 / 7 | 150,000 | 27.0 MB |
| 1M | table-wide tuned | 7 / 7 | 150,000 | 27.0 MB |
| 4M | table-wide tuned | 27 / 27 | 150,000 | 27.3 MB |

(An earlier revision listed 27 parts / ~9 MB at 1M and 107 parts at 4M —
artifacts of the defect's fragmented callbacks.) All runs deliver exact item
totals, `inner_relationships` = row count, and 0 warnings when tuned.

## Bounds

- Destination callback/part: `1,000 x 150 = 150,000` relationships (~27 MB),
  independent of cardinality and of the extract buffer.
- Process peak RSS: flat 1M -> 4M post-mitigation; guarded by the benchmark band.

## Conclusion

Both acceptance bounds hold, and no unsupported upload artifacts are created.
Verified against release tags: only the 1.26 series carries the defect (fixed
in 1.27.0; `buffer_max_items` semantics unchanged through 1.30.0, so the
coordination applies on all versions). If the pin moves past 1.26,
`ensure_dlt_jsonl_batching` becomes a no-op and upstream's corrected iterator
takes over — worth reporting upstream with the minimal reproduction above.
If a future dlt change ever leaves 1.26.x running unpatched,
`writer_buffer_max_items()` reverts to the stock-aligned buffer — the failure
mode is memory, not data corruption.
Future wrapper-width or buffering changes will surface via `peak_rss_per_edge`
and the warnings list.
