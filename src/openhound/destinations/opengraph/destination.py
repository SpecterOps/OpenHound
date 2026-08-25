import logging
import shutil
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

import dlt
from dlt.common import json
from dlt.common.schema import TTableSchema
from dlt.common.storages.file_storage import FileStorage
from dlt.common.storages.load_package import ParsedLoadJobFileName

logger = logging.getLogger(__name__)

DEST_PART: defaultdict[str, int] = defaultdict(int)
DESTINATION_ITEM_BATCH_SIZE = 1000


def _load_items(file_path: str) -> Iterable[dict]:
    """Read normalized JSONL directly to avoid DLT 1.26.x duplicate batches."""
    with FileStorage.open_zipsafe_ro(file_path) as fh:
        for line in fh:
            decoded = json.typed_loads(line)
            if isinstance(decoded, dict):
                yield decoded
            else:
                yield from decoded


def _write_part(
    items: list[dict],
    table_name: str,
    output_path: str,
    source_kind: str,
    part_number: int | None = None,
    job_file_id: str | None = None,
) -> None:
    if part_number is None:
        DEST_PART[table_name] += 1
        part_number = DEST_PART[table_name]
    if job_file_id:
        file_name = f"{table_name}-{job_file_id}-{part_number:04d}.json"
    else:
        file_name = f"{table_name}-{part_number}.json"
    nodes = []
    edges = []
    logger.debug(
        "Processing %d items for OpenGraph file output (part %d)",
        len(items),
        part_number,
    )
    for item in items:
        if item["graph"]["entity_type"] == "node":
            nodes.append(item["graph"]["content"])
        elif item["graph"]["entity_type"] == "edge":
            edges.extend(item["graph"]["content"])

    file_path = Path(output_path) / file_name
    with file_path.open("w", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "graph": {"nodes": nodes, "edges": edges},
                    "metadata": {"source_kind": source_kind},
                }
            ),
        )


def _job_paths(file_path: str, output_path: str) -> tuple[Path, Path, str]:
    """Return retry-stable paths for one DLT load job."""
    parsed = ParsedLoadJobFileName.parse(file_path)
    load_id = Path(file_path).parent.parent.name
    job_id = parsed.job_id()
    root = Path(output_path)
    staging = root / ".openhound-staging" / load_id / job_id
    committed = root / ".openhound-commits" / load_id / f"{job_id}.json"
    return staging, committed, parsed.file_id


def _publish_parts(staging: Path, committed: Path, output_path: str) -> None:
    """Publish a complete staged job and record a completion marker."""
    output_dir = Path(output_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    part_files = sorted(staging.glob("*.json"))
    for part_file in part_files:
        part_file.replace(output_dir / part_file.name)

    committed.parent.mkdir(parents=True, exist_ok=True)
    marker = committed.with_suffix(".tmp")
    marker.write_text(
        json.dumps({"parts": [path.name for path in part_files]}),
        encoding="utf-8",
    )
    marker.replace(committed)
    shutil.rmtree(staging)


@dlt.destination(skip_dlt_columns_and_tables=True, batch_size=0)
def opengraph_file(
    items: str,
    table: TTableSchema,
    output_path: str = dlt.config.value,
    source_kind: str = dlt.config.value,
):

    table_name = table.get("name") or "opengraph"
    staging, committed, file_id = _job_paths(items, output_path)
    if committed.exists():
        logger.debug("OpenGraph destination job %s was already published", file_id)
        return

    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    batch = []
    part_number = 0
    for item in _load_items(items):
        batch.append(item)
        if len(batch) == DESTINATION_ITEM_BATCH_SIZE:
            part_number += 1
            _write_part(
                batch,
                table_name,
                str(staging),
                source_kind,
                part_number,
                file_id,
            )
            batch = []
    if batch:
        part_number += 1
        _write_part(
            batch,
            table_name,
            str(staging),
            source_kind,
            part_number,
            file_id,
        )
    _publish_parts(staging, committed, output_path)
