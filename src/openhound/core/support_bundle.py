import logging
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Glob patterns that match the platform log and all rotated backups.
# The CustomLogger writes to <base_path>/openhound.log and rotates to
# <base_path>/openhound.log.YYYY-MM-DD_HH(-MM-SS)?.
_PLATFORM_LOG_PATTERNS = ["openhound.log", "openhound.log.*"]

# Glob patterns that match extension/job run logs and all rotated backups.
# The CustomLogger writes to <base_path>/ext_<name>.log and rotates to
# <base_path>/ext_<name>.log.YYYY-MM-DD_HH(-MM-SS)?.
_JOB_LOG_PATTERNS = ["ext_*.log", "ext_*.log.*"]


def collect_log_files(log_base_path: Path) -> list[Path]:
    """Collect all current and rotated log files from the log directory.

    Finds the platform log (openhound.log) and all job run logs (ext_*.log),
    including any rotated backup files produced by CustomLogger's
    RotatingFileHandler.

    Args:
        log_base_path: The directory where OpenHound writes its log files.
            This is CustomLogger.base_path after setup() has been called.

    Returns:
        Sorted list of Path objects for each log file found. Empty if the
        directory does not exist or contains no matching files.
    """
    if not log_base_path.is_dir():
        logger.warning(
            f"Log directory does not exist, support bundle will be empty: {log_base_path}"
        )
        return []

    found: set[Path] = set()
    for pattern in _PLATFORM_LOG_PATTERNS + _JOB_LOG_PATTERNS:
        found.update(log_base_path.glob(pattern))

    log_files = sorted(f for f in found if f.is_file())

    if not log_files:
        logger.warning(
            f"No log files found in {log_base_path}; support bundle will be empty."
        )
    else:
        logger.debug(f"Collected {len(log_files)} log file(s) for support bundle.")

    return log_files


def create_support_bundle(collector_name: str, log_base_path: Path) -> Path:
    """Collect all log files and zip them into a named support bundle.

    The zip file is written to a temporary directory so it does not pollute
    the log directory. The caller is responsible for deleting the file after
    it has been uploaded.

    Filename format: <collector_name>_support_bundle_YYYY-MM-DD-HH-MM-SS.zip
    (UTC timestamp, dashes as separators to match the acceptance criteria.)

    Files inside the zip are stored flat (basename only, no directory prefix).
    If two rotated backups share the same basename they will collide; this is
    not expected given CustomLogger's naming conventions.

    Args:
        collector_name: The configured collector name (used in the zip filename).
        log_base_path: The directory where OpenHound writes its log files.

    Returns:
        Path to the created zip file inside a temporary directory.
    """
    timestamp = datetime.now(UTC).strftime("%Y-%m-%d-%H-%M-%S")
    zip_name = f"{collector_name}_support_bundle_{timestamp}.zip"

    tmp_dir = Path(tempfile.mkdtemp())
    zip_path = tmp_dir / zip_name

    log_files = collect_log_files(log_base_path)

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for log_file in log_files:
            zf.write(log_file, arcname=log_file.name)

    logger.info(
        f"Created support bundle '{zip_name}' with {len(log_files)} log file(s) "
        f"at {zip_path}."
    )
    return zip_path
