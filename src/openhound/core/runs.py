"""Persistent, exclusively owned collection workspaces and phase checkpoints."""

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from importlib.metadata import version
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal
from uuid import uuid4

from dlt.common.configuration.container import Container
from dlt.common.configuration.providers.doc import CustomLoaderDocProvider
from dlt.common.configuration.specs.pluggable_run_context import PluggableRunContext
from filelock import FileLock
from pydantic import BaseModel, Field

import openhound

Phase = Literal["collect", "preprocess", "convert"]
Status = Literal["not_started", "running", "failed", "complete"]


class RunManifest(BaseModel):
    """Non-secret identity and checkpoints; DLT owns individual package state."""

    schema_version: Literal[1] = 1
    run_id: str
    collector: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    collector_version: str | None
    openhound_version: str
    dlt_version: str
    job_id: int | str
    bhe_url: str | None = None
    mode: Literal["ingest", "write"]
    status: Status = "not_started"
    phase: Phase | None = None
    results: dict[Phase, list[str]] = Field(default_factory=dict)
    error_type: str | None = None
    recovery_status: Status = "not_started"
    recovery_phase: Phase | None = None
    recovery_results: dict[Phase, list[str]] = Field(default_factory=dict)
    recovery_requested_workers: int | None = None
    recovery_error_type: str | None = None


class SavedRun:
    """A portable workspace whose paths are relative to its manifest."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory.resolve()
        self.manifest = RunManifest.model_validate_json(
            (self.directory / "run.json").read_text()
        )

    @classmethod
    def create(
        cls,
        root: Path,
        *,
        collector: str,
        collector_version: str | None,
        job_id: int | str,
        mode: Literal["ingest", "write"],
        bhe_url: str | None = None,
    ) -> "SavedRun":
        """Allocate a new run; never reuse another job's packages or resources."""
        run_id = uuid4().hex
        manifest = RunManifest(
            run_id=run_id,
            collector=collector,
            collector_version=collector_version,
            openhound_version=openhound.__version__,
            dlt_version=version("dlt"),
            job_id=job_id,
            bhe_url=bhe_url,
            mode=mode,
        )
        directory = root.resolve() / run_id
        directory.mkdir(parents=True, mode=0o700)
        (directory / "run.json").write_text(manifest.model_dump_json(indent=2))
        return cls(directory)

    @property
    def raw(self) -> Path:
        """Filesystem destination for the collection phase."""
        return self.directory / "raw"

    @property
    def lookup(self) -> Path:
        """Lookup built by the original preprocessing phase."""
        return self.directory / "lookup.duckdb"

    @property
    def graph(self) -> Path:
        """Graph files staged for managed jobs."""
        return self.directory / "graph"

    @contextmanager
    def locked(self) -> Iterator[None]:
        """Refuse concurrent collection, inspection, or recovery of this run."""
        with FileLock(self.directory / ".lock", timeout=0):
            # Another owner may have updated the checkpoints before acquisition.
            self.manifest = RunManifest.model_validate_json(
                (self.directory / "run.json").read_text()
            )
            yield

    def save(self) -> None:
        """Atomically publish checkpoints while holding the workspace lock."""
        pending = self.directory / "run.json.tmp"
        with pending.open("w") as stream:
            stream.write(self.manifest.model_dump_json(indent=2))
            stream.flush()
            os.fsync(stream.fileno())
        pending.replace(self.directory / "run.json")

    def write_collection_inventory(self) -> None:
        """Record collected JSONL paths and sizes while holding the run lock.

        This is informational metadata, not an input to recovery or packaging.
        File contents are not read. The caller decides how to handle I/O errors.
        """
        files = [
            {
                "path": path.relative_to(self.directory).as_posix(),
                "size_bytes": path.stat().st_size,
            }
            for path in sorted(self.raw.rglob("*.jsonl.gz"))
            if not path.is_symlink() and path.is_file()
        ]
        inventory = {
            "schema_version": 1,
            "run_id": self.manifest.run_id,
            "files": files,
        }
        pending: Path | None = None
        try:
            with NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                prefix=".collection-inventory-",
                suffix=".tmp",
                dir=self.directory,
                delete=False,
            ) as stream:
                pending = Path(stream.name)
                json.dump(inventory, stream, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            pending.replace(self.directory / "collection-inventory.json")
        finally:
            if pending is not None:
                pending.unlink(missing_ok=True)

    @contextmanager
    def configuration(
        self, *, recovery: bool = False, normalize_workers: int | None = None
    ) -> Iterator[None]:
        """Temporarily isolate DLT state, overriding deployment pipeline paths."""
        base = self.directory / "recovery" if recovery else self.directory
        base.mkdir(exist_ok=True)
        settings = {"pipelines_dir": str(base / "pipelines")}
        document: dict = {"pipelines_dir": settings["pipelines_dir"], "pipelines": {}}
        for suffix in ("collect", "preproc", "convert"):
            name = f"{self.manifest.collector}_{suffix}"
            scoped: dict = dict(settings)
            if normalize_workers is not None:
                scoped["normalize"] = {"workers": normalize_workers}
            document[name] = scoped
            document["pipelines"][name] = scoped
        provider = CustomLoaderDocProvider(
            "Saved run storage", lambda: document, supports_secrets=False
        )
        providers = Container()[PluggableRunContext].providers
        previous = providers.providers
        providers.providers = [provider, *previous]
        try:
            yield
        finally:
            providers.providers = previous

    def inspect(self) -> dict:
        """Describe checkpoints and retained package IDs without attaching DLT."""
        with self.locked():
            packages = {}
            for base in (self.directory, self.directory / "recovery"):
                for pipeline in (base / "pipelines").glob("*"):
                    packages[str(pipeline.relative_to(self.directory))] = {
                        kind: sorted(
                            path.name
                            for path in (pipeline / location).glob("*")
                            if path.is_dir()
                        )
                        for kind, location in (
                            ("extracted", "normalize/extracted"),
                            ("normalized", "load/normalized"),
                        )
                    }
            return {
                "directory": str(self.directory),
                **self.manifest.model_dump(),
                "packages": packages,
            }
