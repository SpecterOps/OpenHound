"""End-to-end benchmark for BED-9372 table-wide edge batching.

Runs the real opengraph source through a real dlt.pipeline and instrumented
destination at the ticket's 100k/1M row scales, recording the required metrics
(wall time, CPU, peak RSS, wrappers, inner relationships, normalized DLT items,
destination callbacks/parts, package files, bytes, and per-callback/part maxima).
LookupManager and the synthetic asset are held constant so gains reflect batching.

Usage:
    python benchmarks/opengraph_batching_benchmark.py --rows 100000 --batch-size 150
    python benchmarks/opengraph_batching_benchmark.py --rows 1000000 --edges-per-row 1

Pass --baseline to force per-row wrapping (batch_size=1) for a before/after run.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

# Support running as a plain script (python benchmarks/opengraph_batching_benchmark.py)
# as well as a module (python -m benchmarks.opengraph_batching_benchmark).
sys.path.insert(0, str(Path(__file__).resolve().parent))

import duckdb  # noqa: E402

from openhound.core.lookup import LookupManager  # noqa: E402

from _bench_assets import ASSET_SHAPES, write_synthetic_input  # noqa: E402
from _bench_run import BenchMetrics, run_pipeline  # noqa: E402


@dataclass
class BenchConfig:
    rows: int
    batch_size: int
    edges_per_row: int
    shape: str
    files: int
    baseline: bool
    keep_output: bool
    output_root: Path
    owns_output_root: bool
    quiet: bool = False


def _parse_args(argv: list[str] | None = None) -> BenchConfig:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--rows", type=int, default=100_000, help="Number of source rows.")
    p.add_argument(
        "--batch-size", type=int, default=150, help="Source edge batch size."
    )
    p.add_argument(
        "--edges-per-row",
        type=int,
        default=1,
        help="Edges emitted per source row (1 = high-cardinality one-edge shape).",
    )
    p.add_argument(
        "--shape",
        choices=sorted(ASSET_SHAPES),
        default="one_edge",
        help="Synthetic asset shape (one_edge, multi_edge, node_and_edge).",
    )
    p.add_argument(
        "--files",
        type=int,
        default=4,
        help="Number of input .jsonl.gz files to spread rows across.",
    )
    p.add_argument(
        "--baseline",
        action="store_true",
        help="Force per-row wrapping (batch_size=1) for a before/after comparison.",
    )
    p.add_argument(
        "--keep-output",
        action="store_true",
        help="Keep the generated input/output instead of using a temp dir.",
    )
    p.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="Directory for input/output (default: a temp dir under the system tmp).",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="Silence DLT INFO logging so the JSON report is the only output.",
    )
    ns = p.parse_args(argv)
    if ns.rows < 1:
        p.error("--rows must be >= 1")
    if ns.batch_size < 1:
        p.error("--batch-size must be >= 1")
    if ns.edges_per_row < 0:
        p.error("--edges-per-row must be >= 0")
    if ns.files < 1:
        p.error("--files must be >= 1")

    owns_output_root = ns.output_root is None
    root = ns.output_root or Path(
        __import__("tempfile").mkdtemp(prefix="openhound-bench-")
    )
    return BenchConfig(
        rows=ns.rows,
        batch_size=1 if ns.baseline else ns.batch_size,
        edges_per_row=ns.edges_per_row,
        shape=ns.shape,
        files=ns.files,
        baseline=ns.baseline,
        keep_output=ns.keep_output,
        output_root=root,
        owns_output_root=owns_output_root,
        quiet=ns.quiet,
    )


def _in_memory_lookup() -> LookupManager:
    # Hold DuckDB/model work constant: a real LookupManager over an empty
    # in-memory schema so lookup cost is fixed and negligible across runs.
    conn = duckdb.connect(":memory:")
    return LookupManager(conn, "main")


def _print_report(cfg: BenchConfig, metrics: BenchMetrics) -> None:
    expected_wrappers = (
        cfg.rows * cfg.edges_per_row
        if cfg.batch_size == 1
        else math.ceil((cfg.rows * cfg.edges_per_row) / cfg.batch_size)
    )
    report = {
        "config": asdict(cfg) | {"output_root": str(cfg.output_root)},
        "expected_edge_wrappers_ceiling": expected_wrappers,
        "metrics": asdict(metrics),
    }
    print(json.dumps(report, indent=2, default=str))


def _silence_dlt_logging() -> None:
    import logging
    import os

    # DLT's log level is env-driven and resolved when the pipeline runs, so the
    # env var is what takes effect; setLevel covers an already-created logger.
    # CRITICAL suppresses DLT's ERROR log for the recovered WinError 5 transient;
    # a real terminal failure still raises regardless of log level.
    os.environ["RUNTIME__LOG_LEVEL"] = "CRITICAL"
    logging.getLogger("dlt").setLevel(logging.CRITICAL)


def main(argv: list[str] | None = None) -> None:
    cfg = _parse_args(argv)
    if cfg.quiet:
        _silence_dlt_logging()
    cfg.output_root.mkdir(parents=True, exist_ok=True)
    input_dir = cfg.output_root / "input"
    output_dir = cfg.output_root / "output"
    work_dir = cfg.output_root / "dlt_work"

    table = write_synthetic_input(
        input_dir, cfg.shape, cfg.rows, cfg.edges_per_row, cfg.files
    )

    metrics = run_pipeline(
        input_dir=input_dir,
        output_dir=output_dir,
        table=table,
        shape=cfg.shape,
        batch_size=cfg.batch_size,
        lookup=_in_memory_lookup(),
        work_dir=work_dir,
    )
    _print_report(cfg, metrics)

    if not cfg.keep_output and cfg.owns_output_root:
        import shutil

        shutil.rmtree(cfg.output_root, ignore_errors=True)


if __name__ == "__main__":
    main()
