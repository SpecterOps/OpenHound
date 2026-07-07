import json
import logging
from pathlib import Path

import pytest

from openhound.core.logging import (
    CustomLogger,
    OpenHoundJSONFormatter,
    OpenHoundTextFormatter,
    RotatingFileHandler,
    logger_override,
)


def test_root_handler_setup():
    """Test that the root logger has a handler configured and that it is a RotatingFileHandler"""
    root_logger = logging.getLogger()
    assert len(root_logger.handlers) > 0, (
        "The root logger should have at least one handler configured"
    )

    base_handler = root_logger.handlers[0]
    assert isinstance(base_handler, RotatingFileHandler), (
        "The root logger should use RotatingFileHandler"
    )
    assert base_handler.baseFilename.endswith("openhound.log"), (
        "The root logger should log to 'openhound.log'"
    )


def test_dlt_handler_setup():
    """Test that the default DLT handler is overwritten by our RotatingFileHandler"""
    dlt_logger = logging.getLogger("dlt")
    assert len(dlt_logger.handlers) > 0, (
        "The default DLT logger should have at least one handler configured"
    )
    assert dlt_logger.propagate is False, (
        "The DLT logger should have propagation disabled to prevent duplicate logs in the root logger"
    )

    base_handler = dlt_logger.handlers[0]
    assert isinstance(base_handler, RotatingFileHandler), (
        "The default DLT logger should use RotatingFileHandler"
    )
    assert base_handler.baseFilename.endswith("openhound.log"), (
        "The default DLT logger should log to 'openhound.log'"
    )


def test_dlt_extension_handlers():
    """Test that the DLT handler is correctly set up for an extension and that the destination filename has changed"""
    logger_override.set_handler("test_extension")
    dlt_logger = logging.getLogger("dlt")
    assert len(dlt_logger.handlers) > 0, (
        "The DLT logger should have handlers configured after setting an extension handler"
    )
    assert dlt_logger.propagate is False, (
        "The DLT logger should have propagation disabled to prevent duplicate logs in the root logger"
    )

    base_handler = dlt_logger.handlers[0]
    assert isinstance(base_handler, RotatingFileHandler), (
        "The DLT logger should use RotatingFileHandler for extension handlers"
    )
    assert base_handler.baseFilename.endswith("ext_test_extension.log"), (
        "The DLT logger should log to 'ext_test_extension.log' for the test_extension handler"
    )


def test_log_routing_content(tmp_path, caplog, monkeypatch):
    """Test that logs are correctly routed and that the files are created for the expected paths"""
    # Pin JSON (dlt's only crash-safe override value) so the JSON-parsing assertions are self-contained.
    monkeypatch.setenv("RUNTIME__LOG_FORMAT", "JSON")
    logger_override.base_path = tmp_path
    logger_override.setup()
    logger_override.set_handler("test_extension")

    root_logger = logging.getLogger()
    dlt_logger = logging.getLogger("dlt")

    with caplog.at_level(logging.INFO):
        with caplog.at_level(logging.INFO, logger="dlt"):
            root_logger.info("Core openhound log")
            dlt_logger.info("Extension DLT log")

    assert (tmp_path / "openhound.log").exists(), (
        "The 'openhound.log' file should exist for the core logs"
    )
    assert (tmp_path / "ext_test_extension.log").exists(), (
        "The 'ext_test_extension.log' file should exist for the extension logs"
    )

    with open(tmp_path / "openhound.log", "r") as core_log_file:
        core_logs = core_log_file.readlines()
        core_logs_json = [json.loads(log) for log in core_logs]

    with open(tmp_path / "ext_test_extension.log", "r") as ext_log_file:
        ext_logs = ext_log_file.readlines()
        ext_logs_json = [json.loads(log) for log in ext_logs]

    assert core_logs_json[0]["message"] == "Core openhound log", (
        "The core log message should be present in 'openhound.log'"
    )
    assert ext_logs_json[0]["message"] == "Extension DLT log", (
        "The extension log message should be present in 'ext_test_extension.log'"
    )


def test_validate_format_defaults_to_text():
    """Test that the log format validation accepts text/json and falls back to text"""
    assert CustomLogger._validate_format("JSON") == "json"
    assert CustomLogger._validate_format("text") == "text"
    assert CustomLogger._validate_format("invalid") == "text"


def test_file_formatter_selection():
    """Test that the configured log_format selects the matching file formatter"""
    json_logger = CustomLogger("openhound.log", log_format="json")
    assert isinstance(json_logger._file_formatter(), OpenHoundJSONFormatter), (
        "log_format 'json' should produce a JSON formatter"
    )

    text_logger = CustomLogger("openhound.log", log_format="text")
    assert isinstance(text_logger._file_formatter(), OpenHoundTextFormatter), (
        "log_format 'text' should produce a plain-text formatter"
    )


def test_text_formatter_produces_plain_text():
    """Test that the text formatter produces a readable, non-JSON line with extras"""
    formatter = OpenHoundTextFormatter()
    record = logging.LogRecord(
        name="openhound.core.collect",
        level=logging.ERROR,
        pathname="collect.py",
        lineno=61,
        msg="Starting collector %s",
        args=("github",),
        exc_info=None,
        func="run",
    )
    record.resource = "scim_users"
    record.taskName = None

    output = formatter.format(record)

    assert "Starting collector github" in output, (
        "The formatted message should be rendered with its args"
    )
    assert "[ERROR" in output, "The log level should be present in the output"
    assert "openhound.core.collect:run:61" in output, (
        "The logger/function/line location should be present in the output"
    )
    assert "resource=scim_users" in output, "Extra fields should be preserved"
    assert "taskName" not in output, "Null extras like taskName should be dropped"

    with pytest.raises(json.JSONDecodeError):
        json.loads(output)


def test_get_file_handler_caches_handler_per_path(tmp_path):
    """The shared handler cache should return the same instance for a given path and
    distinct instances for different paths, so each file is only opened once."""
    custom_logger = CustomLogger("openhound.log", base_path=str(tmp_path))

    path_a = tmp_path / "openhound.log"
    path_b = tmp_path / "ext_test.log"

    handler_a = custom_logger._get_file_handler(path_a)
    try:
        # A repeated lookup (even via a re-built equivalent Path) returns the cache hit
        assert custom_logger._get_file_handler(path_a) is handler_a, (
            "The same path should return the cached handler instance"
        )
        assert custom_logger._get_file_handler(Path(str(path_a))) is handler_a, (
            "Equivalent paths should resolve to the same cached handler"
        )

        # A different path gets its own dedicated handler
        handler_b = custom_logger._get_file_handler(path_b)
        assert handler_b is not handler_a, (
            "A different path should produce a different handler instance"
        )
        assert isinstance(handler_b, RotatingFileHandler), (
            "Cached handlers should be RotatingFileHandler instances"
        )
    finally:
        for handler in custom_logger._file_handlers.values():
            handler.close()


def test_root_and_dlt_loggers_share_single_file_handler(tmp_path, monkeypatch):
    """The root and dlt loggers writing to openhound.log must share one handler
    instance so the file is only opened once, which is required for rotation on
    Windows where an open handle blocks renaming the file."""
    # A sibling module may set RUNTIME__LOG_PATH on import; keep our explicit base_path.
    monkeypatch.delenv("RUNTIME__LOG_PATH", raising=False)
    custom_logger = CustomLogger("openhound.log", base_path=str(tmp_path))

    try:
        custom_logger.setup()

        root_logger = logging.getLogger()
        dlt_logger = logging.getLogger("dlt")

        root_file_handlers = [
            handler
            for handler in root_logger.handlers
            if isinstance(handler, RotatingFileHandler)
        ]
        dlt_file_handlers = [
            handler
            for handler in dlt_logger.handlers
            if isinstance(handler, RotatingFileHandler)
        ]

        assert len(root_file_handlers) == 1, (
            "The root logger should have exactly one rotating file handler"
        )
        assert len(dlt_file_handlers) == 1, (
            "The dlt logger should have exactly one rotating file handler"
        )
        assert root_file_handlers[0] is dlt_file_handlers[0], (
            "Root and dlt loggers must share the same handler instance for openhound.log"
        )
        assert root_file_handlers[0].baseFilename.endswith("openhound.log"), (
            "The shared handler should write to 'openhound.log'"
        )
    finally:
        # Restore the shared global logging state for subsequent tests.
        logger_override.setup()


def test_build_file_handler_applies_rotation_settings(tmp_path):
    """_build_file_handler should produce a RotatingFileHandler configured with the
    logger's rotation settings, the custom extMatch, and the selected formatter."""
    custom_logger = CustomLogger(
        "openhound.log",
        base_path=str(tmp_path),
        backup_count=7,
        max_bytes=1234,
        log_format="json",
    )

    handler = custom_logger._build_file_handler(tmp_path / "openhound.log")
    try:
        assert isinstance(handler, RotatingFileHandler), (
            "The built handler should be a RotatingFileHandler"
        )
        assert handler.backupCount == 7, "The configured backup_count should be applied"
        assert handler.max_bytes == 1234, "The configured max_bytes should be applied"
        assert isinstance(handler.formatter, OpenHoundJSONFormatter), (
            "log_format 'json' should attach the JSON formatter to the handler"
        )
        # The overridden extMatch must recognize both time- and size-based suffixes
        assert handler.extMatch.match("2024-01-02_03"), (
            "extMatch should recognize the time-based rotation suffix"
        )
        assert handler.extMatch.match("2024-01-02_03-04-05"), (
            "extMatch should recognize the size-based rotation suffix"
        )
    finally:
        handler.close()
