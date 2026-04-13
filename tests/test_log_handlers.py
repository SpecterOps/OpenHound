import json
import logging

from openhound.core.logging import RotatingFileHandler, logger_override


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


def test_log_routing_content(tmp_path, caplog):
    """Test that logs are correctly routed and that the files are created for the expected paths"""
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
