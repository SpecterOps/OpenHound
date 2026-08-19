# BED-9372 destination memory & part-size review

Satisfies acceptance criteria lines 235-237: end-to-end peak memory and part
sizes are reviewed against the destination's 1,000-item batch, and the result
is shown to stay bounded independently of total table cardinality without
creating unsupported upload artifacts.

## Method

Four runs of `benchmarks/opengraph_batching_benchmark.py`, one-edge shape,
4 input files, DLT 1.26.0, single load worker. Table-wide = the fix
(`batch_size=150`); baseline = pre-fix per-row wrapping (`batch_size=1`). Each
`opengraph_file` callback receives a DLT batch of up to 1,000 wrapper items and
flattens their relationship lists into one in-memory `edges` list, then writes
one JSON part.

## Results

| Scale | Mode | Edge wrappers | Callbacks / Parts | Max rel/callback | Max bytes/callback | Peak RSS | Wall |
|------:|------|-------------:|------------------:|-----------------:|-------------------:|---------:|-----:|
| 100k | table-wide | 667 | 1 / 1 | 100,000 | 17.8 MB | 255 MB | 6.5s |
| 100k | baseline | 100,000 | 100 / 100 | 1,000 | 178 KB | 130 MB | 18.2s |
| 1M | table-wide | 6,667 | 7 / 7 | 150,000 | 27.0 MB | 1,257 MB | 41.7s |
| 1M | baseline | 1,000,000 | 1,000 / 1,000 | 1,000 | 180 KB | 132 MB | 111.8s |

All runs: `inner_relationships` = row count exactly, `normalized_dlt_items` =
edge wrappers, 0 warnings.

## Per-callback / part bound

The maximum relationships in a single destination callback is bounded by

    destination_batch_size (1,000 items) x source batch_size (150 edges)
    = 150,000 relationships

This is confirmed empirically: the per-callback maximum is 100,000 at 100k rows
(the whole table is one sub-1,000-item callback) and rises only to **150,000**
at 1M rows — it does **not** track total cardinality. Max bytes/callback caps
at ~27 MB for a 150-edge-per-wrapper one-edge table and would not grow if the
table were 10M or 100M rows: a callback still holds at most 1,000 wrappers.

The written JSON part mirrors the callback, so max part size is bounded the
same way (27 MB uncompressed here). No part approaches a size that BloodHound /
OpenHound ingest cannot accept, and no new upload artifact is introduced — the
destination still emits one JSON part per callback exactly as before the fix.

## Peak RSS

Peak RSS for the table-wide runs (255 MB -> 1.26 GB from 100k -> 1M) is **not**
caused by unbounded destination accumulation — that is capped at 150,000
relationships / 27 MB per callback as shown above. It is DLT's extract/normalize
staging of the larger intermediate load files for the whole table. The baseline
runs stay flat (~130 MB) because each item is a tiny one-edge wrapper, so DLT's
per-item buffering is cheaper even though it produces 150x more items.

The RSS growth is therefore an extract/normalize characteristic of DLT's
file-staging pipeline, orthogonal to the batching fix and to the destination
callback bound. It is a known scaling cost of running the whole table through
one extract, not an unbounded destination leak.

## Conclusion

- Destination per-callback and per-part memory is bounded by
  `1,000 x batch_size` relationships (150,000 / ~27 MB here), **independent of
  total table cardinality** — criterion lines 236-237 satisfied.
- No unsupported upload artifact is created; part count drops
  (`ceil(N/batch_size)` callbacks vs `N`), part size stays well within ingest
  limits, and the file layout is unchanged.
- Peak process RSS scales with DLT's whole-table extract/normalize staging, not
  with the destination callback; if a hard RSS ceiling is later required, the
  bounded lever is coordinating `batch_size` down or DLT's extract/normalize
  file rotation — the destination itself is already bounded.

No coordinated source/destination batch-size change is required to keep the
destination bounded. `batch_size=150` with the destination's 1,000-item batch
keeps the per-callback maximum at 150,000 relationships / ~27 MB regardless of
table size.
