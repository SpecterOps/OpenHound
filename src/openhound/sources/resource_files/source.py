from enum import Enum
from pathlib import Path
from typing import Any, Generator

import dlt
from dlt.extract import DltResource
from dlt.sources.filesystem import filesystem, read_csv, read_jsonl, read_parquet


class FileType(str, Enum):
    JSONL = "jsonl"
    PARQUET = "parquet"
    CSV = "csv"


FILETYPE_LOADERS: dict[FileType, Any] = {
    FileType.JSONL: read_jsonl,
    FileType.PARQUET: read_parquet,
    FileType.CSV: read_csv,
}


@dlt.source(name="resource_files", max_table_nesting=0)
def resource_files(
    root: Path,
    resources: dict[str, str],
    glob: str = "**/*.jsonl.gz",
    file_type: FileType = FileType.JSONL,
) -> Generator[DltResource, None, None]:
    if file_type not in FileType:
        raise ValueError(f"Loader '{file_type}' not supported by filesystem readers")

    for resource, path in resources.items():
        resource_path = Path(root) / path
        reader = filesystem(bucket_url=str(resource_path), file_glob=glob)
        yield (reader | FILETYPE_LOADERS[file_type]).with_name(resource)
