"""Pipeline execution and metric collection for the BED-9372 benchmark.

Runs a real dlt.pipeline with the real opengraph source and an instrumented
destination (mirroring opengraph_file), recording per-callback item/
relationship/byte counts plus wall time, process CPU, and peak RSS.
"""

from __future__ import annotations

import gzip
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

# Single load worker reduces the Windows WinError 5 race on DLT's atomic
# os.replace. Must be set before dlt import.
os.environ.setdefault("LOAD__WORKERS", "1")

import dlt  # noqa: E402
import psutil  # noqa: E402
from dlt.common import json as dlt_json  # noqa: E402

from openhound.core.lookup import LookupManager  # noqa: E402
from openhound.sources.opengraph.source import (  # noqa: E402
    GraphResource,
    opengraph,
    writer_buffer_max_items,
)

from _bench_assets import model_for_shape  # noqa: E402
from _peak_rss import PeakRSSSampler  # noqa: E402
from _win_atomic_retry import install as _install_win_atomic_retry  # noqa: E402

# Retry DLT's atomic state-file replace so high-part-count runs survive the
# Windows WinError 5 transient. No-op when the replace succeeds first try.
_install_win_atomic_retry()


@dataclass
class BenchMetrics:
    wall_seconds: float = 0.0
    process_cpu_seconds: float = 0.0
    peak_rss_bytes: int = 0
    edge_wrappers: int = 0
    node_wrappers: int = 0
    inner_relationships: int = 0
    normalized_dlt_items: int = 0
    destination_callbacks: int = 0
    destination_parts: int = 0
    dlt_package_files: int = 0
    part_uncompressed_bytes: int = 0
    part_compressed_bytes: int = 0
    max_relationships_per_callback: int = 0
    max_bytes_per_callback: int = 0
    max_relationships_per_part: int = 0
    max_bytes_per_part: int = 0
    writer_buffer_max_items: int = 0
    peak_rss_per_edge: float = 0.0
    warnings: list[str] = field(default_factory=list)


# RSS guard band: fail if peak RSS exceeds floor + per-edge allowance.
RSS_FLOOR_BYTES = 300 * 1024 * 1024
RSS_PER_EDGE_ALLOWANCE = 512


def _instrumented_destination(real_output_dir: str, metrics: BenchMetrics):
    """Return a dlt destination mirroring opengraph_file's write path (1000-item
    batch, nodes/edges grouping, one JSON part per callback) while recording the
    per-callback metrics the ticket requires.
    """
    part_counter: dict[str, int] = {}

    @dlt.destination(skip_dlt_columns_and_tables=True, batch_size=1000)
    def instrumented(
        items, table, output_path=real_output_dir, source_kind="benchmark"
    ):
        table_name = table.get("name") or "opengraph"
        part_counter[table_name] = part_counter.get(table_name, 0) + 1
        metrics.destination_callbacks += 1

        nodes = []
        edges = []
        for item in items:
            g = item["graph"]
            if g["entity_type"] == "node":
                nodes.append(g["content"])
            if g["entity_type"] == "edge":
                edges.extend(g["content"])

        payload = dlt_json.dumps(
            {
                "graph": {"nodes": nodes, "edges": edges},
                "metadata": {"source_kind": source_kind},
            }
        )
        raw = payload.encode("utf-8") if isinstance(payload, str) else payload
        metrics.max_relationships_per_callback = max(
            metrics.max_relationships_per_callback, len(edges)
        )
        metrics.max_bytes_per_callback = max(metrics.max_bytes_per_callback, len(raw))

        file_path = Path(output_path) / f"{table_name}-{part_counter[table_name]}.json"
        file_path.write_bytes(raw)

    return instrumented(output_path=real_output_dir, source_kind="benchmark")


def _measure_output_parts(output_dir: Path, metrics: BenchMetrics) -> None:
    for part in sorted(output_dir.glob("*.json")):
        raw = part.read_bytes()
        metrics.destination_parts += 1
        metrics.part_uncompressed_bytes += len(raw)
        compressed = len(gzip.compress(raw))
        metrics.part_compressed_bytes += compressed
        try:
            doc = dlt_json.loadb(raw)
            edges = len(doc["graph"]["edges"])
        except Exception:
            edges = 0
        metrics.max_relationships_per_part = max(
            metrics.max_relationships_per_part, edges
        )
        metrics.max_bytes_per_part = max(metrics.max_bytes_per_part, len(raw))


def _count_source_wrappers(
    input_dir: Path, table: str, shape: str, batch_size: int, lookup: LookupManager
) -> tuple[int, int, int]:
    """Iterate the real source once to count edge/node wrappers and inner edges.

    This is a separate pass so the destination timing is not polluted; it uses
    the same source configuration the pipeline run uses.
    """
    model = model_for_shape(shape)
    source = opengraph(
        [GraphResource(table=table, model=model)],
        bucket_url=input_dir.as_uri(),
        lookup=lookup,
        extras={},
        batch_size=batch_size,
    )
    edge_wrappers = node_wrappers = inner = 0
    for item in source.resources[f"{model.__name__.lower()}_fs"]:
        g = item["graph"]
        if g["entity_type"] == "edge":
            edge_wrappers += 1
            inner += len(g["content"])
        else:
            node_wrappers += 1
    return edge_wrappers, node_wrappers, inner


def run_pipeline(
    input_dir: Path,
    output_dir: Path,
    table: str,
    shape: str,
    batch_size: int,
    lookup: LookupManager,
    work_dir: Path,
) -> BenchMetrics:
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    metrics = BenchMetrics()

    # Mirror the Converter's writer-buffer coordination: an explicit override
    # wins while running; the prior environment is restored afterwards so
    # repeated in-process runs never inherit a stale buffer.
    buffer_env = "DATA_WRITER__BUFFER_MAX_ITEMS"
    prior_buffer = os.environ.get(buffer_env)
    if prior_buffer is None:
        os.environ[buffer_env] = str(writer_buffer_max_items(batch_size))
    metrics.writer_buffer_max_items = int(os.environ[buffer_env])
    try:
        metrics.edge_wrappers, metrics.node_wrappers, metrics.inner_relationships = (
            _count_source_wrappers(input_dir, table, shape, batch_size, lookup)
        )

        model = model_for_shape(shape)
        source = opengraph(
            [GraphResource(table=table, model=model)],
            bucket_url=input_dir.as_uri(),
            lookup=lookup,
            extras={},
            batch_size=batch_size,
        )
        dest = _instrumented_destination(str(output_dir), metrics)
        # Isolate DLT's working dir per run so runs never share load packages/state.
        pipeline = dlt.pipeline(
            pipeline_name="bench_opengraph_convert",
            dataset_name="bench",
            destination=dest,
            pipelines_dir=str(work_dir),
        )

        proc = psutil.Process()
        cpu_before = proc.cpu_times()
        sampler = PeakRSSSampler(proc)
        sampler.start()
        t0 = time.perf_counter()
        load_info = pipeline.run(source)
        metrics.wall_seconds = time.perf_counter() - t0
        sampler.stop()
        cpu_after = proc.cpu_times()

        metrics.process_cpu_seconds = (cpu_after.user - cpu_before.user) + (
            cpu_after.system - cpu_before.system
        )
        metrics.peak_rss_bytes = sampler.peak_rss
        if metrics.inner_relationships > 0:
            metrics.peak_rss_per_edge = metrics.peak_rss_bytes / metrics.inner_relationships
            rss_band = RSS_FLOOR_BYTES + RSS_PER_EDGE_ALLOWANCE * metrics.inner_relationships
            if metrics.peak_rss_bytes > rss_band:
                metrics.warnings.append(
                    f"peak RSS {metrics.peak_rss_bytes} exceeds guard band "
                    f"{rss_band} (floor {RSS_FLOOR_BYTES} + "
                    f"{RSS_PER_EDGE_ALLOWANCE} B/edge); staging memory may be "
                    "scaling with table cardinality"
                )
        _measure_output_parts(output_dir, metrics)
        _collect_dlt_metrics(pipeline, load_info, metrics)
    finally:
        if prior_buffer is None:
            del os.environ[buffer_env]
        else:
            os.environ[buffer_env] = prior_buffer
    return metrics


def _collect_dlt_metrics(pipeline, load_info, metrics: BenchMetrics) -> None:
    # Normalized DLT items come from the normalize step's per-job writer metrics
    # (items_count), excluding DLT's internal pipeline-state table.
    try:
        trace = pipeline.last_trace
        for step in trace.steps:
            if step.step != "normalize":
                continue
            for _load_id, runs in step.step_info.metrics.items():
                for run in runs:
                    for fname, writer in run["job_metrics"].items():
                        if fname.startswith("_dlt_pipeline_state"):
                            continue
                        metrics.normalized_dlt_items += int(
                            getattr(writer, "items_count", 0)
                        )
    except Exception as exc:  # pragma: no cover - defensive: trace shape drift
        metrics.warnings.append(f"normalize metrics unavailable: {exc}")

    # DLT package files: completed load jobs excluding the internal state table.
    try:
        for package in load_info.load_packages:
            for job in package.jobs["completed_jobs"]:
                if job.job_file_info.table_name.startswith("_dlt_pipeline_state"):
                    continue
                metrics.dlt_package_files += 1
    except Exception as exc:  # pragma: no cover - defensive: LoadInfo shape drift
        metrics.warnings.append(f"load package metrics unavailable: {exc}")
