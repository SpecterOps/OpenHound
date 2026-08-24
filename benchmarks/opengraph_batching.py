"""Manually benchmark OpenGraph source batching and file-destination output.

This is deliberately not a pytest test.  Run one mode at a time, for example:

    .\\.venv\\Scripts\\python.exe benchmarks\\opengraph_batching.py \
        --rows 100000 --batch-size 150 --mode per-row

Then repeat with ``--mode page-batched``. To replay real saved graph output, use
``--graph-dir graph/okta --graph-glob 'applicationuser_fs-*.json'``. Each
invocation creates a timestamped subdirectory and writes a ``metrics.json``
report, so prior benchmark outputs are never overwritten.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib
import json as stdlib_json
import threading
import time
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Self

import psutil
from pydantic import PrivateAttr

from openhound.core.asset import BaseAsset
from openhound.core.models.entries_dataclass import Edge, EdgePath
from openhound.destinations.opengraph.destination import (
    DEST_PART,
    DESTINATION_ITEM_BATCH_SIZE,
    _load_items,
    _write_part,
)
from openhound.sources.opengraph.source import (
    READ_JSONL_PAGE_SIZE,
    _generate_graph_content,
)

Mode = Literal["per-row", "page-batched"]

RAW_REPLAY_MODELS = {
    ("okta", "application_users"): (
        "openhound_okta.models",
        "ApplicationUser",
        "openhound_okta.lookup",
        "OktaLookup",
    ),
    ("github", "org_role_members"): (
        "openhound_github.models",
        "OrgRoleMember",
        "openhound_github.lookup",
        "GithubLookup",
    ),
}


class SyntheticAsset(BaseAsset):
    """A generic membership-like asset with a configurable edge count."""

    row: int
    edges_per_row: int
    _lookup: object = PrivateAttr(default=None)
    _extras: dict = PrivateAttr(default_factory=dict)

    @property
    def as_node(self):
        return None

    @property
    def edges(self) -> Iterable[Edge]:
        return (
            Edge(
                kind="Benchmark_MemberOf",
                start=EdgePath(match_by="id", value=f"principal-{self.row}-{edge}"),
                end=EdgePath(match_by="id", value=f"group-{self.row}-{edge}"),
            )
            for edge in range(self.edges_per_row)
        )


class PeakRss:
    """Sample process RSS while a benchmark is running."""

    def __init__(self) -> None:
        self._process = psutil.Process()
        self._stop = threading.Event()
        self._peak = self._process.memory_info().rss
        self._thread = threading.Thread(target=self._sample, daemon=True)

    def _sample(self) -> None:
        while not self._stop.wait(0.02):
            self._peak = max(self._peak, self._process.memory_info().rss)

    def __enter__(self) -> Self:
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._thread.join()
        self._peak = max(self._peak, self._process.memory_info().rss)

    @property
    def bytes(self) -> int:
        return self._peak


class SemanticDigest:
    """Track ordered and order-independent hashes of flattened graph content."""

    def __init__(self) -> None:
        self._sequence = hashlib.sha256()
        self._individual: list[str] = []
        self.count = 0

    def add(self, content: dict) -> None:
        canonical = stdlib_json.dumps(
            content, default=str, separators=(",", ":"), sort_keys=True
        ).encode()
        self._sequence.update(canonical + b"\n")
        self._individual.append(hashlib.sha256(canonical).hexdigest())
        self.count += 1

    def metrics(self) -> dict[str, int | str]:
        multiset = hashlib.sha256("\n".join(sorted(self._individual)).encode()).hexdigest()
        return {
            "count": self.count,
            "sequence_sha256": self._sequence.hexdigest(),
            "multiset_sha256": multiset,
        }


def _pages(rows: int, edges_per_row: int) -> Iterator[list[dict[str, int]]]:
    for start in range(0, rows, READ_JSONL_PAGE_SIZE):
        yield [
            {"row": row, "edges_per_row": edges_per_row}
            for row in range(start, min(start + READ_JSONL_PAGE_SIZE, rows))
        ]


def _content(
    rows: int, edges_per_row: int, batch_size: int, mode: Mode
) -> Iterator[dict]:
    for page in _pages(rows, edges_per_row):
        if mode == "per-row":
            for row in page:
                yield from _generate_graph_content([row], SyntheticAsset, batch_size)
        else:
            yield from _generate_graph_content(page, SyntheticAsset, batch_size)


def _replay_graph_content(files: Iterable[Path], batch_size: int) -> Iterator[dict]:
    """Re-wrap final OpenGraph files for a destination-path replay.

    Final graph files no longer retain raw source-row or DLT-page provenance, so
    this measures real relationship shape and file-destination behavior, not
    source-model evaluation or an exact before/after source batching comparison.
    """
    edge_parts: list[dict] = []
    for file_path in files:
        document = stdlib_json.loads(file_path.read_text(encoding="utf-8"))
        graph = document.get("graph", {})
        for node in graph.get("nodes", []):
            yield {"graph": {"content": node, "entity_type": "node"}}
        for edge in graph.get("edges", []):
            edge_parts.append(edge)
            if len(edge_parts) == batch_size:
                yield {"graph": {"content": edge_parts, "entity_type": "edge"}}
                edge_parts = []
    if edge_parts:
        yield {"graph": {"content": edge_parts, "entity_type": "edge"}}


def _replay_raw_content(
    files: Iterable[Path],
    model: type[BaseAsset],
    batch_size: int,
    lookup: object | None,
    row_counter: dict[str, int],
    mode: Mode,
) -> Iterator[dict]:
    """Replay raw DLT JSONL files through the real extension asset model."""

    def apply_context(asset: BaseAsset) -> None:
        asset._lookup = lookup
        asset._extras = {}

    for file_path in files:
        opener = gzip.open if file_path.suffix == ".gz" else open
        with opener(file_path, "rt", encoding="utf-8") as fh:
            page = []
            for line in fh:
                page.append(stdlib_json.loads(line))
                if len(page) == READ_JSONL_PAGE_SIZE:
                    row_counter["rows"] += len(page)
                    if mode == "per-row":
                        for row in page:
                            yield from _generate_graph_content(
                                [row], model, batch_size, apply_context
                            )
                    else:
                        yield from _generate_graph_content(
                            page, model, batch_size, apply_context
                        )
                    page = []
            if page:
                row_counter["rows"] += len(page)
                if mode == "per-row":
                    for row in page:
                        yield from _generate_graph_content(
                            [row], model, batch_size, apply_context
                        )
                else:
                    yield from _generate_graph_content(
                        page, model, batch_size, apply_context
                    )


def _raw_model_and_lookup(args: argparse.Namespace) -> tuple[type[BaseAsset], object | None]:
    model_module_name, model_name, lookup_module_name, lookup_name = RAW_REPLAY_MODELS[
        (args.raw_source, args.raw_table)
    ]
    model = getattr(importlib.import_module(model_module_name), model_name)
    if not args.lookup_file:
        return model, None

    import duckdb

    connection = duckdb.connect(str(args.lookup_file), read_only=True)
    lookup_class = getattr(importlib.import_module(lookup_module_name), lookup_name)
    return model, lookup_class(connection)


def _write_normalized_load_file(
    content: Iterable[dict], load_file: Path
) -> tuple[int, int, dict[str, dict[str, int | str]]]:
    wrappers = 0
    relationships = 0
    nodes = SemanticDigest()
    edges = SemanticDigest()
    with gzip.open(load_file, "wt", encoding="utf-8") as fh:
        for item in content:
            wrappers += 1
            if item["graph"]["entity_type"] == "edge":
                relationships += len(item["graph"]["content"])
                for edge in item["graph"]["content"]:
                    edges.add(edge)
            else:
                nodes.add(item["graph"]["content"])
            fh.write(stdlib_json.dumps(item, separators=(",", ":")) + "\n")
    return wrappers, relationships, {"nodes": nodes.metrics(), "edges": edges.metrics()}


def _write_destination_parts(load_file: Path, output_dir: Path) -> tuple[int, int, int]:
    batch: list[dict] = []
    parts = 0
    max_relationships = 0
    max_part_bytes = 0
    DEST_PART.clear()

    def flush() -> None:
        nonlocal batch, parts, max_relationships, max_part_bytes
        relationships = sum(
            len(item["graph"]["content"])
            for item in batch
            if item["graph"]["entity_type"] == "edge"
        )
        _write_part(batch, "synthetic_fs", str(output_dir), "benchmark")
        part_file = output_dir / f"synthetic_fs-{DEST_PART['synthetic_fs']}.json"
        parts += 1
        max_relationships = max(max_relationships, relationships)
        max_part_bytes = max(max_part_bytes, part_file.stat().st_size)
        batch = []

    for item in _load_items(str(load_file)):
        batch.append(item)
        if len(batch) == DESTINATION_ITEM_BATCH_SIZE:
            flush()
    if batch:
        flush()
    return parts, max_relationships, max_part_bytes


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int)
    parser.add_argument("--batch-size", type=int, default=150)
    parser.add_argument("--edges-per-row", type=int, default=1)
    parser.add_argument("--mode", choices=("per-row", "page-batched"))
    parser.add_argument(
        "--graph-dir",
        type=Path,
        help="Directory containing final graph JSON files, such as graph/okta",
    )
    parser.add_argument(
        "--graph-glob",
        default="*_fs-*.json",
        help="Glob within --graph-dir to replay (default: *_fs-*.json)",
    )
    parser.add_argument(
        "--raw-dir",
        type=Path,
        help="Raw DLT output root, such as output/okta or output/github",
    )
    parser.add_argument("--raw-source", choices=("okta", "github"))
    parser.add_argument("--raw-table")
    parser.add_argument(
        "--compare-source-batching",
        action="store_true",
        help="Compare legacy per-row and page-batched raw conversion semantics",
    )
    parser.add_argument(
        "--lookup-file",
        type=Path,
        help="Optional DuckDB lookup file for model-dependent relationship generation",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(".benchmark-results"),
        help="Ignored directory for timestamped artifacts (default: .benchmark-results)",
    )
    args = parser.parse_args()
    if args.batch_size <= 0 or args.edges_per_row < 0:
        parser.error("edges-per-row must be non-negative; batch-size must be positive")
    if args.raw_dir:
        if args.graph_dir or args.rows is not None or args.mode is not None:
            parser.error("--raw-dir cannot be combined with --graph-dir, --rows, or --mode")
        if not args.raw_source or not args.raw_table:
            parser.error("--raw-dir requires --raw-source and --raw-table")
        if (args.raw_source, args.raw_table) not in RAW_REPLAY_MODELS:
            supported = ", ".join(
                f"{source}/{table}" for source, table in RAW_REPLAY_MODELS
            )
            parser.error(f"Unsupported raw replay target; supported: {supported}")
        if not args.raw_dir.is_dir():
            parser.error(f"raw directory does not exist: {args.raw_dir}")
        if args.lookup_file and not args.lookup_file.is_file():
            parser.error(f"lookup file does not exist: {args.lookup_file}")
    elif args.compare_source_batching:
        parser.error("--compare-source-batching requires --raw-dir")
    elif args.graph_dir:
        if args.rows is not None or args.mode is not None:
            parser.error("--graph-dir cannot be combined with --rows or --mode")
        if not args.graph_dir.is_dir():
            parser.error(f"graph directory does not exist: {args.graph_dir}")
    elif args.rows is None or args.mode is None:
        parser.error("synthetic runs require both --rows and --mode")
    elif args.rows < 0:
        parser.error("rows must be non-negative")
    return args


def main() -> None:
    args = _parse_args()
    run_name = datetime.now(UTC).strftime("openhound-batching-%Y%m%dT%H%M%SZ")
    run_dir = args.output_dir / run_name
    suffix = 1
    while run_dir.exists():
        suffix += 1
        run_dir = args.output_dir / f"{run_name}-{suffix}"
    run_dir.mkdir(parents=True)

    load_file = run_dir / "synthetic.normalized.jsonl.gz"
    graph_files = []
    raw_files = []
    raw_row_counter = {"rows": 0}
    if args.graph_dir:
        graph_files = sorted(args.graph_dir.glob(args.graph_glob))
        if not graph_files:
            raise ValueError(
                f"No graph files matched {args.graph_glob!r} in {args.graph_dir}"
            )
    if args.raw_dir:
        raw_files = sorted((args.raw_dir / args.raw_table).glob("*.jsonl*"))
        if not raw_files:
            raise ValueError(f"No raw JSONL files found in {args.raw_dir / args.raw_table}")
        model, lookup = _raw_model_and_lookup(args)

    legacy_semantics = None
    legacy_wrappers = None
    if args.compare_source_batching:
        legacy_rows = {"rows": 0}
        legacy_content = _replay_raw_content(
            raw_files,
            model,
            args.batch_size,
            lookup,
            legacy_rows,
            "per-row",
        )
        legacy_wrappers = 0
        legacy_nodes = SemanticDigest()
        legacy_edges = SemanticDigest()
        for item in legacy_content:
            legacy_wrappers += 1
            if item["graph"]["entity_type"] == "edge":
                for edge in item["graph"]["content"]:
                    legacy_edges.add(edge)
            else:
                legacy_nodes.add(item["graph"]["content"])
        legacy_semantics = {
            "rows": legacy_rows["rows"],
            "wrapper_items": legacy_wrappers,
            "nodes": legacy_nodes.metrics(),
            "edges": legacy_edges.metrics(),
        }
    process = psutil.Process()
    cpu_start = process.cpu_times()
    wall_start = time.perf_counter()
    with PeakRss() as peak_rss:
        wrappers, relationships, semantics = _write_normalized_load_file(
            _replay_raw_content(
                raw_files,
                model,
                args.batch_size,
                lookup,
                raw_row_counter,
                "page-batched",
            )
            if args.raw_dir
            else _replay_graph_content(graph_files, args.batch_size)
            if args.graph_dir
            else _content(args.rows, args.edges_per_row, args.batch_size, args.mode),
            load_file,
        )
        parts, max_relationships, max_part_bytes = _write_destination_parts(
            load_file, run_dir
        )
    wall_seconds = time.perf_counter() - wall_start
    cpu_end = process.cpu_times()
    destination_files = list(run_dir.glob("synthetic_fs-*.json"))

    metrics = {
        "mode": "raw-replay" if args.raw_dir else "graph-replay" if args.graph_dir else args.mode,
        "rows": raw_row_counter["rows"] if args.raw_dir else args.rows,
        "edges_per_row": args.edges_per_row,
        "graph_input_directory": str(args.graph_dir) if args.graph_dir else None,
        "graph_input_glob": args.graph_glob if args.graph_dir else None,
        "graph_input_files": len(graph_files),
        "raw_input_directory": str(args.raw_dir) if args.raw_dir else None,
        "raw_input_table": args.raw_table if args.raw_dir else None,
        "raw_input_files": len(raw_files),
        "lookup_file": str(args.lookup_file) if args.lookup_file else None,
        "flattened_semantics": semantics,
        "legacy_per_row_comparison": legacy_semantics,
        "source_batch_size": args.batch_size,
        "source_page_size": READ_JSONL_PAGE_SIZE,
        "wrapper_items": wrappers,
        "inner_relationships": relationships,
        "normalized_dlt_items": wrappers,
        "destination_callbacks": 1,
        "destination_parts": parts,
        "dlt_package_files": None,
        "post_package_files": len(destination_files),
        "maximum_relationships_per_part": max_relationships,
        "maximum_part_bytes": max_part_bytes,
        "normalized_compressed_bytes": load_file.stat().st_size,
        "destination_uncompressed_bytes": sum(path.stat().st_size for path in destination_files),
        "wall_seconds": wall_seconds,
        "process_cpu_seconds": (cpu_end.user - cpu_start.user)
        + (cpu_end.system - cpu_start.system),
        "peak_rss_bytes": peak_rss.bytes,
        "notes": [
            "Synthetic benchmark; it does not measure model evaluation, DuckDB, or DLT package creation."
            if not args.graph_dir and not args.raw_dir
            else "Raw replay evaluates extension model conversion but does not create a DLT load package."
            if args.raw_dir
            else "Graph replay uses final OpenGraph output; raw source-row and DLT-page provenance is unavailable.",
            "dlt_package_files is null because this benchmark streams a synthetic normalized load file rather than running a DLT pipeline.",
        ],
    }
    (run_dir / "metrics.json").write_text(
        stdlib_json.dumps(metrics, indent=2) + "\n", encoding="utf-8"
    )
    if legacy_semantics:
        legacy_graph = {key: legacy_semantics[key] for key in ("nodes", "edges")}
        if legacy_graph != semantics:
            raise AssertionError(
                "Legacy per-row and page-batched flattened graph semantics differ; "
                f"see {run_dir / 'metrics.json'}"
            )
    print("\nOpenGraph batching benchmark")
    print(f"  Mode:                  {metrics['mode']}")
    if args.graph_dir:
        print(
            "  Graph files / relations: "
            f"{metrics['graph_input_files']:,} / {metrics['inner_relationships']:,}"
        )
    else:
        print(
            f"  Rows / relationships:  {metrics['rows']:,} / "
            f"{metrics['inner_relationships']:,}"
        )
    print(f"  Wrapper items:         {metrics['wrapper_items']:,}")
    if legacy_semantics:
        print(
            "  Legacy / new wrappers: "
            f"{legacy_wrappers:,} / {metrics['wrapper_items']:,} (semantics match)"
        )
    print(
        "  Destination:           "
        f"{metrics['destination_callbacks']:,} callback, "
        f"{metrics['destination_parts']:,} part(s)"
    )
    print(f"  Wall / CPU:            {metrics['wall_seconds']:.3f}s / {metrics['process_cpu_seconds']:.3f}s")
    print(f"  Peak RSS:              {metrics['peak_rss_bytes'] / 1024 / 1024:.1f} MiB")
    print(
        "  Output bytes:          "
        f"{metrics['normalized_compressed_bytes']:,} compressed, "
        f"{metrics['destination_uncompressed_bytes']:,} destination"
    )
    print(f"  Metrics:               {run_dir / 'metrics.json'}")
    print(f"  Artifacts:             {run_dir}")


if __name__ == "__main__":
    main()
