import os

os.environ.setdefault("RUNTIME__DLTHUB_TELEMETRY", "false")

import json
import logging
import os
import re
import sys
import time
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from copy import copy
from enum import Enum
from logging.handlers import QueueHandler, QueueListener, TimedRotatingFileHandler
from multiprocessing.managers import SyncManager
from pathlib import Path
from threading import RLock
from typing import Any

import dlt
from rich.console import Console
from rich.logging import RichHandler
from rich.markup import escape

import openhound
from openhound.config import get_mode_defaults


class _ValueRedactor(logging.Filter):
    def __init__(self, values: tuple[str, ...]):
        super().__init__()
        self.values = tuple(value for value in values if value)
        self.handlers: set[logging.Handler] = set()

    def _redact(self, value):
        if isinstance(value, str):
            for secret in self.values:
                value = value.replace(secret, "[REDACTED]")
            return value
        if isinstance(value, dict):
            return {self._redact(k): self._redact(v) for k, v in value.items()}
        if isinstance(value, (tuple, list)):
            return type(value)(self._redact(item) for item in value)
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return self._redact(str(value))

    def filter(self, record: logging.LogRecord) -> bool:
        # Preserve argument types during interpolation, then redact the result.
        record.msg = self._redact(record.getMessage())
        record.args = ()
        if record.exc_info:
            record.exc_text = self._redact(
                "".join(traceback.format_exception(*record.exc_info))
            )
            # Rich handlers can format the exception again, including locals.
            record.exc_info = None
        record.__dict__.update(
            self._redact(
                {
                    key: value
                    for key, value in record.__dict__.items()
                    if key not in ("msg", "args")
                }
            )
        )
        return True


_active_redactors: list[_ValueRedactor] = []
_worker_handler: "_WorkerQueueHandler | None" = None
_worker_context: dict[str, Any] = {}
_worker_extension: str | None = None


class _WorkerQueueHandler(QueueHandler):
    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        # QueueHandler's default prepare folds exceptions into msg and discards
        # exc_text. Keep them separate for JSON/text/Rich and avoid duplication.
        record = copy(record)
        for key, value in _worker_context.items():
            record.__dict__.setdefault(key, value)
        if _worker_extension and record.name.split(".")[0] == "dlt":
            record._openhound_extension = _worker_extension
        # Normalize non-serializable extras and format exceptions in the worker.
        # Redact the complete record before even the queue proxy serializes it.
        _ValueRedactor(()).filter(record)
        for redactor in _active_redactors:
            redactor.filter(record)
        return record


def _install_worker_handlers() -> None:
    assert _worker_handler is not None
    root = logging.getLogger()
    root.handlers[:] = [_worker_handler]
    dlt_logger = logging.getLogger("dlt")
    dlt_logger.handlers[:] = [_worker_handler]
    dlt_logger.propagate = False
    dlt_logger.disabled = False


def configure_worker_logging(queue: Any, level: int) -> None:
    """Executor initializer: workers forward, and only the parent owns files."""
    global _worker_handler
    loggers = [logging.getLogger(), *logging.Logger.manager.loggerDict.values()]
    handlers = set()
    for logger in loggers:
        if isinstance(logger, logging.Logger):
            handlers.update(logger.handlers)
            logger.handlers.clear()
            logger.propagate = True
    for handler in handlers:
        handler.close()
    logger_override._file_handlers.clear()
    _worker_handler = _WorkerQueueHandler(queue)
    logging.getLogger().setLevel(level)
    logging.getLogger("dlt").setLevel(level)
    # Queue proxy connection diagnostics must never recurse into that same queue.
    multiprocessing_logger = logging.getLogger("multiprocessing")
    multiprocessing_logger.handlers[:] = [logging.NullHandler()]
    multiprocessing_logger.propagate = False
    _install_worker_handlers()


@contextmanager
def worker_log_context(job_id: int | str, collector: str) -> Iterator[None]:
    global _worker_context, _worker_extension
    previous, extension = _worker_context, _worker_extension
    _worker_context = {"job_id": job_id, "collector_extension": collector}
    _worker_extension = None
    try:
        yield
    finally:
        _worker_context, _worker_extension = previous, extension


def _destination(handler: logging.Handler) -> Any:
    if isinstance(handler, logging.FileHandler):
        return ("file", handler.baseFilename)
    if isinstance(handler, RichHandler):
        return ("stream", id(handler.console.file))
    if isinstance(handler, logging.StreamHandler):
        return ("stream", id(handler.stream))
    return ("handler", id(handler))


class _ParentWorkerHandler(logging.Handler):
    def __init__(self, configuration: "CustomLogger"):
        super().__init__()
        self.configuration = configuration

    def emit(self, record: logging.LogRecord) -> None:
        # Read current destinations so parent-only handlers and reconfiguration
        # take effect for workers too. Always include the main destinations,
        # including for DLT's normally non-propagating logger.
        handlers = list(logging.getLogger().handlers)
        logger = logging.getLogger(record.name)
        while logger.parent is not None:
            handlers.extend(logger.handlers)
            if not logger.propagate:
                break
            logger = logger.parent
        extension = getattr(record, "_openhound_extension", None)
        if extension:
            config = self.configuration
            try:
                path = config._is_valid_path(
                    config.base_path, config.base_path / f"ext_{extension}.log"
                )
                handlers.append(config._get_file_handler(path))
            except (ValueError, OSError):
                # Main destinations still receive the record if an optional
                # extension file is unavailable. Never report supplied paths.
                sys.stderr.write("OpenHound could not open worker extension log\n")
        seen = set()
        for handler in handlers:
            destination = _destination(handler)
            if destination not in seen and record.levelno >= handler.level:
                seen.add(destination)
                try:
                    handler.handle(record)
                except Exception:  # noqa: BLE001 - isolate arbitrary parent handlers
                    # One custom destination must not kill the listener thread
                    # and strand every other worker logging destination.
                    sys.stderr.write("OpenHound worker logging destination failed\n")


class WorkerLogRouter:
    """Parent-owned logging resources for one scheduler, across pool resets.

    A manager-backed multiprocessing queue has no worker feeder thread or shared
    write lock: killing a producer cannot corrupt the listener's input pipe.
    Its server and listener are explicitly stopped after all executors exit.
    """

    def __init__(self, context: Any, configuration: "CustomLogger | None" = None):
        self.context = context
        self.configuration = configuration or logger_override
        self.manager: SyncManager | None = None
        self.listener: QueueListener | None = None
        self._queue: Any = None

    @property
    def queue(self) -> Any:
        if self.manager is None:
            self.manager = SyncManager(ctx=self.context)
            try:
                self.manager.start()
                self._queue = self.manager.Queue()
                self.listener = QueueListener(
                    self._queue, _ParentWorkerHandler(self.configuration)
                )
                self.listener.start()
            except BaseException:
                self.close()
                raise
        return self._queue

    def close(self) -> None:
        # Workers have exited before the sentinel; synchronous proxy puts mean
        # every completed log submission is already ahead of it in the queue.
        try:
            if self.listener is not None:
                self.listener.stop()
        finally:
            self.listener = None
            if self.manager is not None and hasattr(self.manager, "shutdown"):
                self.manager.shutdown()
            self.manager = None
            self._queue = None


def _protect_handler[H: logging.Handler](handler: H) -> H:
    for redactor in _active_redactors:
        handler.addFilter(redactor)
        redactor.handlers.add(handler)
    return handler


@contextmanager
def redact_log_values(values: tuple[str, ...]) -> Iterator[None]:
    """Protect current and newly configured OpenHound handlers for one worker."""
    redactor = _ValueRedactor(values)
    _active_redactors.append(redactor)
    loggers = [logging.getLogger(), *logging.Logger.manager.loggerDict.values()]
    for logger in loggers:
        if isinstance(logger, logging.Logger):
            for handler in logger.handlers:
                _protect_handler(handler)
    try:
        yield
    finally:
        _active_redactors.remove(redactor)
        for handler in redactor.handlers:
            handler.removeFilter(redactor)


VALID_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

VALID_FORMATS = ["json", "text"]

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

            base_file_size = (
                os.path.getsize(self.baseFilename)
                if os.path.exists(self.baseFilename)
                else 0
            )
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
            "openhound_version": openhound.__version__,
        }

        if record.exc_text:
            log_data["exception"] = record.exc_text
        elif record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        for key, value in record.__dict__.items():
            if (
                not key.startswith("_")
                and key not in DEFAULT_LOG_FIELDS
                and key not in log_data
            ):
                log_data[key] = value

        return json.dumps(log_data)


class OpenHoundTextFormatter(logging.Formatter):
    """Human-readable plain-text formatter for OpenHound file logs"""

    def format(self, record: logging.LogRecord) -> str:
        """Format a log record as a single readable line with structured context.

        Args:
            record (logging.LogRecord): The original log record

        Returns:
            str: A human-readable plain-text representation of the log record
        """
        timestamp = self.formatTime(record, "%Y-%m-%d %H:%M:%S")
        location = f"{record.name}:{record.funcName}:{record.lineno}"
        log_line = (
            f"{timestamp} [{record.levelname}] {location} - {record.getMessage()}"
        )

        # Append any custom/extra fields (e.g. resource, phase, extension) so the
        # structured context is preserved without the noise of full JSON
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if (
                not key.startswith("_")
                and key not in DEFAULT_LOG_FIELDS
                and key != "taskName"
                and value is not None
            )
        }
        if extras:
            extra_str = " ".join(f"{key}={value}" for key, value in extras.items())
            log_line = f"{log_line} | {extra_str}"

        if record.exc_text:
            log_line = f"{log_line}\n{record.exc_text}"
        elif record.exc_info:
            log_line = f"{log_line}\n{self.formatException(record.exc_info)}"

        return log_line


class OpenHoundRichFormatter(logging.Formatter):
    """Custom formatter for Rich when logging in CLI mode"""

    def format(self, record: logging.LogRecord) -> str:
        """Custom format log record for Rich with openhound version and other details

        Args:
            record (logging.LogRecord): The original log record

        Returns:
            str: A formatted string for Rich logging
        """
        log_fmt = f"time={self.formatTime(record, '%Y-%m-%d %H:%M:%S')}, msg={record.getMessage()} (openhound_version={openhound.__version__})"
        if record.exc_text and not record.exc_info:
            # The redactor cleared exc_info, so Rich cannot render the original.
            # Treat the sanitized traceback (including [REDACTED]) as literal text.
            log_fmt = f"{log_fmt}\n{escape(record.exc_text)}"
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
        log_format: str | None = None,
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
        self._log_format_override = log_format
        self.log_format = self._validate_format(log_format or "text")

        self.base_path = Path(base_path) if base_path else self.default_platform_path()
        self.log_file_path: Path | None = None

        # Share one rotating handler per file across loggers; opening the same file
        # with multiple handlers breaks rotation on Windows (WinError 32).
        self._file_handlers: dict[Path, RotatingFileHandler] = {}
        self._file_handler_lock = RLock()

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
    def _validate_format(log_format: str, default: str = "text") -> str:
        normalized_format = log_format.lower()
        if normalized_format in VALID_FORMATS:
            return normalized_format

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
        global _worker_extension
        if _worker_handler is not None:
            _worker_extension = name
            _install_worker_handlers()
            return
        log_file_path = self.base_path / f"ext_{name}.log"
        valid_path = self._is_valid_path(self.base_path, log_file_path)
        self.dlt_logger.setLevel(self.root_logger.level)
        self.dlt_logger.handlers.clear()
        self.handlers[self.runtime_mode](self.dlt_logger, valid_path)
        self.dlt_logger.propagate = False

    def setup(self) -> None:
        """Set the correct logging handler based on the detected mode"""

        if _worker_handler is not None:
            _install_worker_handlers()
            return

        # Get values from DLT config if set
        dlt_level = dlt.config.get("runtime.log_level", str)
        dlt_max_bytes = dlt.config.get("runtime.log_max_bytes", int)
        dlt_backup_count = dlt.config.get("runtime.log_backup_count", int)
        dlt_rotate_when = dlt.config.get("runtime.log_rotate_when", str)
        dlt_interval = dlt.config.get("runtime.log_interval", int)
        dlt_cli_level = dlt.config.get("runtime.log_cli_level", str)
        dlt_log_path = dlt.config.get("runtime.log_path", str)
        # dlt only treats the exact value "JSON" specially. We read it to select json logs and
        # fall back to text for anything else (including dlt's default format string).
        dlt_log_format = dlt.config.get("runtime.log_format", str)

        # Check if the DLT config values are valid and if so override the defaults
        self.level = self._validate_level(dlt_level or self.level, default="INFO")
        self.cli_level = self._validate_level(
            dlt_cli_level or self.cli_level, default="ERROR"
        )
        mode_log_format = get_mode_defaults().log_format
        self.log_format = self._validate_format(
            dlt_log_format or self._log_format_override or mode_log_format,
            default=mode_log_format,
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

    def _file_formatter(self) -> logging.Formatter:
        """Return the formatter for file/stdout handlers based on the configured log format.

        Returns:
            logging.Formatter: A JSON formatter when log_format is 'json', otherwise a
            human-readable plain-text formatter.
        """
        if self.log_format == "json":
            return OpenHoundJSONFormatter()
        return OpenHoundTextFormatter()

    def _build_file_handler(self, file_path: Path) -> "RotatingFileHandler":
        """Create and configure a rotating file handler for the given path."""
        rotating_file_handler = RotatingFileHandler(
            file_path,
            when=self.rotate_when,
            interval=self.interval,
            backupCount=self.backup_count,
            max_bytes=self.max_bytes,
            delay=True,
        )
        rotating_file_handler.setFormatter(self._file_formatter())
        # This regular expression overrides the default extMatch to recognize both
        # default time based rotation filenames and size based rotation filenames (which gets a seconds added as well)
        rotating_file_handler.extMatch = re.compile(
            r"(?<!\d)\d{4}-\d{2}-\d{2}_\d{2}(-\d{2}-\d{2})?(?!\d)", re.ASCII
        )
        return rotating_file_handler

    def _get_file_handler(self, file_path: Path) -> "RotatingFileHandler":
        """Return a shared rotating file handler for the path (one open file per path)."""
        key = Path(file_path).resolve()
        # Parent reconfiguration and the worker listener can request the same
        # extension file concurrently. Keep a single rotation owner per path.
        with self._file_handler_lock:
            handler = self._file_handlers.get(key)
            if handler is None:
                handler = self._build_file_handler(file_path)
                self._file_handlers[key] = handler
            else:
                # setup() may be called again after configuration changes. Keep a
                # cached handler's formatter aligned with the newly resolved mode.
                handler.setFormatter(self._file_formatter())
            return _protect_handler(handler)

    def container_handlers(self, logger: logging.Logger, file_path: Path) -> None:
        """Set the logging handler/format when running in a container"""

        formatter = self._file_formatter()

        # Log to stdout for better compatibility with container-based logging systems.
        # Output is human-readable text by default; set runtime.log_format = "JSON" for structured JSON.
        stdout_handler = logging.StreamHandler(sys.stdout)
        stdout_handler.setFormatter(formatter)
        logger.addHandler(_protect_handler(stdout_handler))

        # But also log the same format to a file for persistence and debugging when needed
        logger.addHandler(self._get_file_handler(file_path))

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
        logger.addHandler(_protect_handler(console_handler))

        # But also save the logs to a file using the configured format (text by default)
        logger.addHandler(self._get_file_handler(file_path))

    def service_handlers(self, logger: logging.Logger, file_path: Path) -> None:
        """Set the logging handler/format when running the OpenHound service"""
        logger.addHandler(self._get_file_handler(file_path))

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
