import json
import logging
import os
import re
import sys
import time
from enum import Enum
from importlib.metadata import version
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import dlt
from rich.console import Console
from rich.logging import RichHandler

__version__ = version("openhound")

VALID_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

# This should be a complete list of default fields as part of a LogRecord
# this is used to prevent custom fields overwriting the default LogRecord entries
DEFAULT_LOG_FIELDS = [
    "name",
    "msg",
    "args",
    "created",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "message",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "thread",
    "threadName",
    "exc_info",
    "exc_text",
    "stack_info",
    "asctime",
    "msecs",
]


class LogMode(str, Enum):
    CLI = "cli"
    CONTAINER = "container"
    SERVICE = "service"


class RotatingFileHandler(TimedRotatingFileHandler):
    """A custom rotating file handler which combines the TimeRotatingFileHandler with an additional max_bytes limit.

    Args:
        TimedRotatingFileHandler: The original TimedRotatingFileHandler which rotates logs based on intervals
    """

    def __init__(
        self,
        filename,
        when="h",
        interval=1,
        backupCount=5,
        encoding=None,
        delay=False,
        utc=False,
        max_bytes: int = 0,
    ):
        """Overrides the TimedRotatingFileHandler with an additional max_bytes parameter.
        all of the args are the same as TimedRotatingFileHandler.

        Args:
            filename (_type_): The name of the log file to write to.
            when (str, optional): The type of interval for log rotation. Defaults to "h", can be: "S" (seconds), "M" (minutes), "H" (hours), "D" (days), "midnight", "W0"-"W6" (weekday, 0=Monday).
            interval (int, optional): The interval at which to rotate the logs based on the 'when' parameter. Defaults to 1.
            backupCount (int, optional): The number of backup files to keep. Defaults to 0.
            encoding (_type_, optional): The encoding to use for the log file. Defaults to None.
            delay (bool, optional): Whether to delay file opening until the first log message is emitted. Defaults to False.
            utc (bool, optional): Whether to use UTC for the log timestamps. Defaults to False which is the systems local timestamp.
            max_bytes (int, optional): The maximum file size in bytes before rotation. Defaults to 0 which means no size limit, only based on time.
        """
        super().__init__(filename, when, interval, backupCount, encoding, delay, utc)
        self.max_bytes = max_bytes
        self._size_triggered = False

    def shouldRollover(self, record: logging.LogRecord) -> bool:
        self._size_triggered = False
        if super().shouldRollover(record):
            return True

        if self.max_bytes > 0:
            # TODO: Look into using the stream to read the actual size
            # if self.stream is None:
            #     self.stream = self._open()
            #     size = self.stream.seek(0, os.SEEK_END)

            base_file_size = os.path.getsize(self.baseFilename)
            if base_file_size >= self.max_bytes:
                self._size_triggered = True
                return True

        return False

    def doRollover(self):
        """Override doRollover to handle both time based rollovers and file size based rollovers"""
        if self._size_triggered:
            # If the rollover was triggered by file size, also add the minutes+seconds to the suffix to prevent
            # the default TimedRotatingFileHandler condition from skipping the rollover since the default
            # filename may already exist due to the standard naming convention
            original_suffix = self.suffix
            self.suffix = time.strftime("%Y-%m-%d_%H-%M-%S")
            super().doRollover()
            self.suffix = original_suffix
        else:
            super().doRollover()


class OpenHoundJSONFormatter(logging.Formatter):
    """Custom JSON formatter"""

    def format(self, record: logging.LogRecord) -> str:
        """Custom JSON format log record for OpenHound with version and other details

        Args:
            record (logging.LogRecord): The original log record

        Returns:
            str: A JSON-formatted string representing the log record
        """
        log_data = {
            "timestamp": self.formatTime(record, "%Y-%m-%d %H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "message": record.getMessage(),
            "openhound_version": __version__,
        }

        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        for key, value in record.__dict__.items():
            if (
                not key.startswith("_")
                and key not in DEFAULT_LOG_FIELDS
                and key not in log_data
            ):
                log_data[key] = value

        return json.dumps(log_data)


class OpenHoundRichFormatter(logging.Formatter):
    """Custom formatter for Rich when logging in CLI mode"""

    def format(self, record: logging.LogRecord) -> str:
        """Custom format log record for Rich with openhound version and other details

        Args:
            record (logging.LogRecord): The original log record

        Returns:
            str: A formatted string for Rich logging
        """
        log_fmt = f"time={self.formatTime(record, '%Y-%m-%d %H:%M:%S')}, msg={record.getMessage()} (openhound_version={__version__})"
        return log_fmt


class CustomLogger:
    def __init__(
        self,
        name: str,
        level: str = "INFO",
        max_bytes: int = 3000 * 1024 * 1024,
        rotate_when: str = "midnight",
        backup_count: int = 14,
        interval: int = 1,
        cli_level: str = "ERROR",
        base_path: str | None = None,
    ):
        self.level = level
        self.name = name
        self.root_logger = logging.getLogger()
        self.dlt_logger = logging.getLogger("dlt")
        self.max_bytes = max_bytes
        self.rotate_when = rotate_when
        self.backup_count = backup_count
        self.interval = interval
        self.cli_level = cli_level

        self.base_path = Path(base_path) if base_path else self.default_platform_path()
        self.log_file_path: Path | None = None

        self.handlers = {
            LogMode.CLI: self.cli_handlers,
            LogMode.CONTAINER: self.container_handlers,
            LogMode.SERVICE: self.service_handlers,
        }

    @staticmethod
    def _validate_level(level: str, default: str = "INFO") -> str:
        normalized_level = level.upper()
        if normalized_level in VALID_LEVELS:
            return normalized_level

        return default

    @staticmethod
    def _is_valid_path(base_path: Path, spec_path: Path) -> Path:
        """Validate the user provided path to prevent directory traversal and ensure it's within the base path

        Args:
            base_path (Path): The base path for logs
            spec_path (Path): The user provided path for the log file

        Returns:
            Path: A validated path within the base path
        """
        resolved_spec_path = spec_path.resolve()
        resolved_base_path = base_path.resolve()
        if not resolved_spec_path.is_relative_to(resolved_base_path):
            raise ValueError(
                f"Potential path traversal. Extension attempted to load path: {resolved_spec_path}"
            )
        return resolved_spec_path

    @staticmethod
    def default_platform_path() -> Path:
        """Get the default log path based on platform

        Returns:
            Path: The default log path for the platform
        """
        platform = sys.platform
        match platform:
            case "darwin":
                return Path("~/Library/Logs/openhound").expanduser()
            case "linux":
                return Path("~/.local/share/openhound/logs").expanduser()
            case "win32":
                return (
                    Path(os.environ.get("LOCALAPPDATA", "C:\\ProgramData"))
                    / "openhound"
                    / "logs"
                )
            case _:
                raise ValueError(f"Unsupported platform: {platform}")

    def set_handler(self, name: str) -> None:
        """Set the logging handler for a specific extension or pipeline based on the name

        Args:
            name (str): Name of the extension or pipeline to create a specific logger for
        """
        log_file_path = self.base_path / f"ext_{name}.log"
        valid_path = self._is_valid_path(self.base_path, log_file_path)
        self.dlt_logger.setLevel(self.root_logger.level)
        self.dlt_logger.handlers.clear()
        self.handlers[self.runtime_mode](self.dlt_logger, valid_path)
        self.dlt_logger.propagate = False

    def setup(self) -> None:
        """Set the correct logging handler based on the detected mode"""

        # Get values from DLT config if set
        dlt_level = dlt.config.get("runtime.log_level", str)
        dlt_max_bytes = dlt.config.get("runtime.log_max_bytes", int)
        dlt_backup_count = dlt.config.get("runtime.log_backup_count", int)
        dlt_rotate_when = dlt.config.get("runtime.log_rotate_when", str)
        dlt_interval = dlt.config.get("runtime.log_interval", int)
        dlt_cli_level = dlt.config.get("runtime.log_cli_level", str)
        dlt_log_path = dlt.config.get("runtime.log_path", str)

        # Check if the DLT config values are valid and if so override the defaults
        self.level = self._validate_level(dlt_level or self.level, default="INFO")
        self.cli_level = self._validate_level(
            dlt_cli_level or self.cli_level, default="ERROR"
        )

        # Override the base path if log_path is set in DLT config, otherwise use the default platform path
        self.base_path = Path(dlt_log_path) if dlt_log_path else self.base_path
        self.base_path.mkdir(parents=True, exist_ok=True)
        self.log_file_path = self.base_path / self.name

        # Override the rotation settings if set in DLT config, otherwise use the defaults
        self.max_bytes = dlt_max_bytes if dlt_max_bytes else self.max_bytes
        self.rotate_when = dlt_rotate_when if dlt_rotate_when else self.rotate_when
        self.backup_count = dlt_backup_count if dlt_backup_count else self.backup_count
        self.interval = dlt_interval if dlt_interval else self.interval

        # Clear the default DLT log handlers and set our own handlers based on the runtime mode
        self.dlt_logger.handlers.clear()
        self.handlers[self.runtime_mode](self.dlt_logger, self.log_file_path)
        self.dlt_logger.propagate = False

        # Set the root logger level and handlers based on the runtime mode
        self.root_logger.setLevel(getattr(logging, self.level))
        self.root_logger.handlers.clear()
        self.handlers[self.runtime_mode](self.root_logger, self.log_file_path)

    def container_handlers(self, logger: logging.Logger, file_path: Path) -> None:
        """Set the logging handler/format when running in a container"""

        json_formatter = OpenHoundJSONFormatter()

        # Log to stdout in JSON for better compatibility with container-based logging systems
        stdout_handler = logging.StreamHandler(sys.stdout)
        stdout_handler.setFormatter(json_formatter)
        logger.addHandler(stdout_handler)

        # But also log the same json format to a file for persistence and debugging when needed
        rotating_file_handler = RotatingFileHandler(
            file_path,
            when=self.rotate_when,
            interval=self.interval,
            backupCount=self.backup_count,
            max_bytes=self.max_bytes,
        )
        rotating_file_handler.setFormatter(json_formatter)
        # This regular expression overrides the default extMatch to recognize both
        # default time based rotation filenames and size based rotation filenames (which gets a seconds added as well)
        rotating_file_handler.extMatch = re.compile(
            r"(?<!\d)\d{4}-\d{2}-\d{2}_\d{2}(-\d{2}-\d{2})?(?!\d)", re.ASCII
        )

        logger.addHandler(rotating_file_handler)

    def cli_handlers(self, logger: logging.Logger, file_path: Path) -> None:
        """Set the logging handler/format when running as a standalone CLI tool"""
        # This is used for when running in a terminal and want rich formatting for better readability
        rich_formatter = OpenHoundRichFormatter()
        # Validate cli_level is a valid log level, fallback to ERROR if not

        console_handler = RichHandler(
            level=getattr(logging, self.cli_level),
            console=Console(stderr=True),
            show_time=False,
            show_path=True,
            markup=True,
            rich_tracebacks=True,
        )
        console_handler.setFormatter(rich_formatter)
        logger.addHandler(console_handler)

        # But also save the logs to a file in JSON format :)
        json_formatter = OpenHoundJSONFormatter()
        rotating_file_handler = RotatingFileHandler(
            file_path,
            when=self.rotate_when,
            interval=self.interval,
            backupCount=self.backup_count,
            max_bytes=self.max_bytes,
        )
        rotating_file_handler.setFormatter(json_formatter)
        # This regular expression overrides the default extMatch to recognize both
        # default time based rotation filenames and size based rotation filenames (which gets a seconds added as well)
        rotating_file_handler.extMatch = re.compile(
            r"(?<!\d)\d{4}-\d{2}-\d{2}_\d{2}(-\d{2}-\d{2})?(?!\d)", re.ASCII
        )

        logger.addHandler(rotating_file_handler)

    def service_handlers(self, logger: logging.Logger, file_path: Path) -> None:
        """Set the logging handler/format when running the OpenHound service"""
        json_formatter = OpenHoundJSONFormatter()
        rotating_file_handler = RotatingFileHandler(
            file_path,
            when=self.rotate_when,
            interval=self.interval,
            backupCount=self.backup_count,
            max_bytes=self.max_bytes,
        )
        rotating_file_handler.setFormatter(json_formatter)
        # This regular expression overrides the default extMatch to recognize both
        # default time based rotation filenames and size based rotation filenames (which gets a seconds added as well)
        rotating_file_handler.extMatch = re.compile(
            r"(?<!\d)\d{4}-\d{2}-\d{2}_\d{2}(-\d{2}-\d{2})?(?!\d)", re.ASCII
        )
        logger.addHandler(rotating_file_handler)

    @property
    def runtime_mode(self) -> LogMode:
        """Sort of detect the runtime mode based on environment variables and TTY. This is used to automatically
        switch logging modes

        Returns:
            LogMode: The detected runtime mode
        """
        if os.getenv("LOG_CONTAINER") or os.getenv("KUBERNETES_SERVICE_HOST"):
            return LogMode.CONTAINER
        if sys.stdout.isatty():
            return LogMode.CLI
        return LogMode.SERVICE


logger_override = CustomLogger("openhound.log")
logger_override.setup()
