"""Create manual-upload archives from completed recovery graph files."""

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from pydantic import ValidationError

from openhound.core.models.graph import Graph
from openhound.core.runs import SavedRun


def _committed_parts(graph: Path) -> list[Path]:
    """Select only published parts listed by the graph destination's markers."""
    names: set[str] = set()
    for marker in sorted((graph / ".openhound-commits").glob("*/*.json")):
        if not marker.resolve().is_relative_to(graph):
            raise ValueError(
                "Recovery commit marker points outside the graph directory"
            )
        try:
            document = json.loads(marker.read_bytes())
        except (ValueError, UnicodeError):
            raise ValueError("Invalid recovery commit marker") from None
        parts = document.get("parts") if isinstance(document, dict) else None
        if not isinstance(parts, list):
            # This is malformed serialized data, not an invalid API argument.
            raise ValueError("Invalid recovery commit marker")  # noqa: TRY004
        for name in parts:
            if (
                not isinstance(name, str)
                or "/" in name
                or "\\" in name
                or name.startswith(".")
                or not name.endswith(".json")
                or name in names
            ):
                raise ValueError(
                    "Invalid or duplicate graph part in recovery commit markers"
                )
            part = graph / name
            if part.is_symlink() or not part.is_file():
                raise ValueError(f"Missing or non-regular recovery graph part: {name}")
            names.add(name)
    if not names:
        raise ValueError("Recovery has no committed graph files to package")
    return [graph / name for name in sorted(names)]


def _sha256(path: Path) -> str:
    """Hash an archive without loading the whole package into memory."""
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def package_saved_run(run: SavedRun, output: Path) -> tuple[Path, Path]:
    """Validate recovery files and publish a ZIP plus an external audit manifest.

    Graph bytes are preserved. No collection, DLT execution, or upload occurs.
    Existing output files are never replaced. The ZIP is published last, after
    both artifacts have been built, so a visible ZIP has its manifest alongside.
    """
    output = output.resolve()
    if output.suffix.lower() != ".zip":
        raise ValueError("Output must have a .zip extension")
    sidecar = output.with_name(output.name + ".manifest.json")
    with run.locked():
        if run.manifest.recovery_status != "complete":
            raise ValueError("Recovery must complete before creating an upload package")
        graph = run.directory / "recovery" / "graph"
        if not graph.is_dir() or graph.resolve() != graph:
            raise ValueError(
                "Recovery graph directory is missing or is a symbolic link"
            )
        if output.is_relative_to(graph):
            raise ValueError(
                "Choose an output path outside the recovery graph directory"
            )
        if output.exists() or sidecar.exists():
            raise FileExistsError(
                "Package or manifest already exists; choose another output path"
            )
        parts = _committed_parts(graph)
        manifest: dict[str, Any] = {
            "schema_version": 1,
            "created_at": datetime.now(UTC).isoformat(),
            "run_id": run.manifest.run_id,
            "job_id": run.manifest.job_id,
            "collector": run.manifest.collector,
            "collector_version": run.manifest.collector_version,
            "openhound_version": run.manifest.openhound_version,
            "dlt_version": run.manifest.dlt_version,
            "files": [],
            "node_count": 0,
            "edge_count": 0,
        }
        # Temporary files share the output filesystem, enabling atomic,
        # no-overwrite publication with hard links, including concurrent runs.
        with TemporaryDirectory(
            prefix=".openhound-package-", dir=output.parent
        ) as temp:
            archive = Path(temp) / "graph.zip"
            archive.touch(mode=0o600)
            with ZipFile(
                archive, "w", compression=ZIP_DEFLATED, allowZip64=True
            ) as bundle:
                for part in parts:
                    content = part.read_bytes()
                    try:
                        validated = Graph.model_validate_json(content, strict=True)
                    except ValidationError:
                        raise ValueError(
                            f"Invalid OpenGraph document: {part.name}"
                        ) from None
                    bundle.writestr(part.name, content)
                    manifest["files"].append(
                        {
                            "name": part.name,
                            "size_bytes": len(content),
                            "sha256": hashlib.sha256(content).hexdigest(),
                            "node_count": len(validated.graph.nodes),
                            "edge_count": len(validated.graph.edges),
                        }
                    )
                    manifest["node_count"] += len(validated.graph.nodes)
                    manifest["edge_count"] += len(validated.graph.edges)
            if not manifest["node_count"] and not manifest["edge_count"]:
                raise ValueError("Recovery graph contains no nodes or edges to upload")
            manifest["package_sha256"] = _sha256(archive)
            receipt = Path(temp) / "manifest.json"
            receipt.touch(mode=0o600)
            receipt.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            os.link(receipt, sidecar)
            try:
                os.link(archive, output)
            except OSError:
                sidecar.unlink()
                raise
    return output, sidecar
