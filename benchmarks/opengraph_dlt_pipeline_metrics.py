"""Run a real local DLT/OpenGraph load and report package and callback metrics.

Example:
    .\\.venv\\Scripts\\python.exe benchmarks\\opengraph_dlt_pipeline_metrics.py --rows 100000
"""

from __future__ import annotations

import argparse
import gzip
import json
import tempfile
from collections.abc import Iterable
from pathlib import Path

import dlt

from openhound.core.asset import BaseAsset
from openhound.core.models.entries_dataclass import Edge, EdgePath
from openhound.destinations.opengraph import destination as destination_module
from openhound.sources.opengraph.source import GraphResource, opengraph


class PipelineBenchmarkAsset(BaseAsset):
    """One relationship per raw input row."""

    row: int

    @property
    def as_node(self):
        return None

    @property
    def edges(self) -> Iterable[Edge]:
        yield Edge(
            kind="PipelineBenchmarkEdge",
            start=EdgePath(match_by="id", value=f"principal-{self.row}"),
            end=EdgePath(match_by="id", value=f"group-{self.row}"),
        )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, required=True)
    parser.add_argument("--batch-size", type=int, default=150)
    parser.add_argument("--output-dir", type=Path, default=Path(".benchmark-results"))
    args = parser.parse_args()
    if args.rows < 0 or args.batch_size <= 0:
        parser.error("rows must be non-negative and batch-size must be positive")
    return args


def main() -> None:
    args = _parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix="openhound-dlt-", dir=args.output_dir))
    raw_dir = run_dir / "raw" / "assets"
    raw_dir.mkdir(parents=True)
    with gzip.open(raw_dir / "rows.jsonl.gz", "wt", encoding="utf-8") as raw_file:
        for row in range(args.rows):
            raw_file.write(json.dumps({"row": row}) + "\n")

    callback_items: list[int] = []
    callback_bytes: list[int] = []
    original_load_items = destination_module._load_items

    def measured_load_items(file_path: str):
        callback_bytes.append(Path(file_path).stat().st_size)
        count = 0
        for item in original_load_items(file_path):
            count += 1
            yield item
        callback_items.append(count)

    destination_module._load_items = measured_load_items
    try:
        pipeline = dlt.pipeline(
            pipeline_name="opengraph_dlt_pipeline_benchmark",
            dataset_name="opengraph_dlt_pipeline_benchmark",
            destination=destination_module.opengraph_file(
                output_path=str(run_dir / "graph"), source_kind="benchmark"
            ),
            pipelines_dir=str(run_dir / "pipelines"),
        )
        load_info = pipeline.run(
            opengraph(
                [GraphResource(table="assets", model=PipelineBenchmarkAsset)],
                bucket_url=str(run_dir / "raw"),
                lookup=None,
                batch_size=args.batch_size,
            )
        )
    finally:
        destination_module._load_items = original_load_items

    completed_jobs = [
        job
        for package in load_info.load_packages
        for job in package.jobs["completed_jobs"]
    ]
    graph_parts = list((run_dir / "graph").glob("pipelinebenchmarkasset_fs-*.json"))
    metrics = {
        "rows": args.rows,
        "source_batch_size": args.batch_size,
        "dlt_package_files": len(completed_jobs),
        "destination_callbacks": len(callback_items),
        "maximum_items_per_destination_callback": max(callback_items, default=0),
        "maximum_bytes_per_destination_callback": max(callback_bytes, default=0),
        "destination_parts": len(graph_parts),
    }
    (run_dir / "dlt_metrics.json").write_text(json.dumps(metrics, indent=2) + "\n")
    print(json.dumps(metrics, indent=2))
    print(f"Metrics: {run_dir / 'dlt_metrics.json'}")


if __name__ == "__main__":
    main()
