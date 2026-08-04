"""Create uploadable support bundles from OpenHound logs."""

import logging
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_LOG_PATTERNS = (
    "openhound.log",
    "openhound.log.*",
    "ext_*.log",
    "ext_*.log.*",
)


def collect_log_files(log_base_path: Path) -> list[Path]:
    """Return current and rotated platform and extension logs, sorted by path."""
    if not log_base_path.is_dir():
        logger.warning("Log directory does not exist: %s", log_base_path)
        return []

    files = {
        path
        for pattern in _LOG_PATTERNS
        for path in log_base_path.glob(pattern)
        if path.is_file()
    }
    return sorted(files)


def create_support_bundle(collector_name: str, log_base_path: Path) -> Path:
    """Archive logs in a temporary ZIP; the caller must remove the returned file."""
    timestamp = datetime.now(UTC).strftime("%Y-%m-%d-%H-%M-%S")
    bundle_path = (
        Path(tempfile.mkdtemp())
        / f"{collector_name}_support_bundle_{timestamp}.zip"
    )

    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for log_file in collect_log_files(log_base_path):
            archive.write(log_file, arcname=log_file.name)

    logger.info("Created support bundle at %s", bundle_path)
    return bundle_path
