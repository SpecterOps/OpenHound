import json
import logging
import os
import threading
from concurrent.futures.process import BrokenProcessPool
from contextlib import nullcontext
from decimal import Decimal
from io import StringIO
from pathlib import Path

import dlt
import pytest
from rich.console import Console
from rich.logging import RichHandler

from openhound.core.logging import (
    CustomLogger,
    LogMode,
    OpenHoundJSONFormatter,
    OpenHoundRichFormatter,
    OpenHoundTextFormatter,
    RotatingFileHandler,
    _ParentWorkerHandler,
    _ValueRedactor,
    redact_log_values,
)


@pytest.fixture
def configured_logger(tmp_path, monkeypatch):
    """Set up real root/DLT loggers without retaining process-wide changes."""
    original_get = dlt.config.get

    def get(field, expected_type):
        if field.startswith("runtime."):
            return {
                "runtime.log_path": str(tmp_path),
                "runtime.log_format": "JSON",
            }.get(field)
        return original_get(field, expected_type)

    monkeypatch.setattr(dlt.config, "get", get)
    monkeypatch.setattr(
        CustomLogger, "runtime_mode", property(lambda self: LogMode.SERVICE)
    )
    custom_logger = CustomLogger("openhound.log", base_path=str(tmp_path))
    original = [
        (logger, logger.handlers[:], logger.level, logger.propagate)
        for logger in (custom_logger.root_logger, custom_logger.dlt_logger)
    ]
    try:
        custom_logger.setup()
        yield custom_logger
    finally:
        for logger, handlers, level, propagate in original:
            logger.handlers[:] = handlers
            logger.setLevel(level)
            logger.propagate = propagate
        for handler in custom_logger._file_handlers.values():
            handler.close()


@pytest.fixture
def no_log_format_override(monkeypatch):
    """Remove the resolved override, including values from local TOML files."""
    original_get = dlt.config.get

    def get(field, expected_type):
        if field == "runtime.log_format":
            return None
        return original_get(field, expected_type)

    monkeypatch.setattr(dlt.config, "get", get)


def test_root_handler_setup(configured_logger):
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


def test_dlt_handler_setup(configured_logger):
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


def test_dlt_extension_handlers(configured_logger):
    """Test that the DLT handler is correctly set up for an extension and that the destination filename has changed"""
    configured_logger.set_handler("test_extension")
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


def test_log_routing_content(configured_logger, tmp_path, caplog):
    """Test that logs are correctly routed and that the files are created for the expected paths"""
    configured_logger.set_handler("test_extension")

    root_logger = logging.getLogger()
    dlt_logger = logging.getLogger("dlt")

    with caplog.at_level(logging.INFO), caplog.at_level(logging.INFO, logger="dlt"):
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


def test_managed_mode_writes_structured_file_logs(
    tmp_path, monkeypatch, no_log_format_override
):
    """Managed mode should default rotating file logs to newline-delimited JSON."""
    monkeypatch.setenv("OPENHOUND__MANAGED", "true")
    monkeypatch.setenv("RUNTIME__LOG_PATH", str(tmp_path))
    custom_logger = CustomLogger("managed.log", base_path=str(tmp_path))
    # Keep this setup isolated from the process-wide loggers used by other tests.
    custom_logger.root_logger = logging.getLogger("managed-test")
    custom_logger.dlt_logger = logging.getLogger("managed-test-dlt")
    custom_logger.root_logger.propagate = False
    custom_logger.dlt_logger.propagate = False

    try:
        custom_logger.setup()
        custom_logger.root_logger.info(
            "Collection started", extra={"job_id": 42, "collector": "aws"}
        )

        log_record = json.loads((tmp_path / "managed.log").read_text())
        assert log_record["message"] == "Collection started"
        assert log_record["job_id"] == 42
        assert log_record["collector"] == "aws"
        assert isinstance(
            custom_logger.root_logger.handlers[0].formatter,
            OpenHoundJSONFormatter,
        )
    finally:
        for handler in custom_logger._file_handlers.values():
            handler.close()
        custom_logger.root_logger.handlers.clear()
        custom_logger.dlt_logger.handlers.clear()


def test_explicit_log_format_overrides_managed_default(tmp_path, monkeypatch):
    """Operators can retain the existing runtime override in managed mode."""
    monkeypatch.setenv("OPENHOUND__MANAGED", "true")
    monkeypatch.setenv("RUNTIME__LOG_FORMAT", "text")
    monkeypatch.setenv("RUNTIME__LOG_PATH", str(tmp_path))
    custom_logger = CustomLogger("managed.log", base_path=str(tmp_path))
    custom_logger.root_logger = logging.getLogger("managed-override-test")
    custom_logger.dlt_logger = logging.getLogger("managed-override-test-dlt")
    custom_logger.root_logger.propagate = False
    custom_logger.dlt_logger.propagate = False

    try:
        custom_logger.setup()

        assert isinstance(
            custom_logger.root_logger.handlers[0].formatter,
            OpenHoundTextFormatter,
        )
    finally:
        for handler in custom_logger._file_handlers.values():
            handler.close()
        custom_logger.root_logger.handlers.clear()
        custom_logger.dlt_logger.handlers.clear()


def test_mode_change_refreshes_cached_file_handler(
    tmp_path, monkeypatch, no_log_format_override
):
    """Re-running setup should apply a changed mode to an existing log file."""
    monkeypatch.setenv("RUNTIME__LOG_PATH", str(tmp_path))
    custom_logger = CustomLogger("mode-change.log", base_path=str(tmp_path))
    custom_logger.root_logger = logging.getLogger("mode-change-test")
    custom_logger.dlt_logger = logging.getLogger("mode-change-test-dlt")
    custom_logger.root_logger.propagate = False
    custom_logger.dlt_logger.propagate = False

    try:
        monkeypatch.setenv("OPENHOUND__MANAGED", "false")
        custom_logger.setup()
        file_handler = custom_logger._get_file_handler(tmp_path / "mode-change.log")
        assert isinstance(file_handler.formatter, OpenHoundTextFormatter)

        monkeypatch.setenv("OPENHOUND__MANAGED", "true")
        custom_logger.setup()

        assert (
            custom_logger._get_file_handler(tmp_path / "mode-change.log")
            is file_handler
        )
        assert isinstance(file_handler.formatter, OpenHoundJSONFormatter)
    finally:
        for handler in custom_logger._file_handlers.values():
            handler.close()
        custom_logger.root_logger.handlers.clear()
        custom_logger.dlt_logger.handlers.clear()


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


@pytest.mark.parametrize(
    "message,args,secret,expected",
    [
        ("Key %d", (12345,), "12345", "Key [REDACTED]"),
        (
            "Key %s costs %.2f",
            ("example-secret-key", Decimal("12.34")),
            "example-secret-key",
            "Key [REDACTED] costs 12.34",
        ),
        (
            "Key %(key)s costs %(cost).2f",
            ({"key": "example-secret-key", "cost": Decimal("12.34")},),
            "example-secret-key",
            "Key [REDACTED] costs 12.34",
        ),
        (
            "Key %r",
            (Path("example-secret-key"),),
            "example-secret-key",
            f"Key {Path('[REDACTED]')!r}",
        ),
    ],
    ids=["numeric-secret", "typed-positional", "typed-mapping", "object-repr"],
)
def test_redaction_formats_original_arguments(message, args, secret, expected):
    record = logging.LogRecord("test", logging.INFO, __file__, 1, message, args, None)
    record.response = {"credential": secret, "count": 7}
    redactor = _ValueRedactor((secret,))

    for formatter in (OpenHoundTextFormatter(), OpenHoundJSONFormatter()):
        # Each handler filters the same record; subsequent passes must be safe.
        assert redactor.filter(record)
        assert record.msg == record.getMessage() == expected
        assert record.args == ()
        assert record.response == {"credential": "[REDACTED]", "count": 7}
        # Numeric metadata (timestamps/PIDs) is preserved and may coincidentally
        # contain a short numeric test secret. Check rendered/string fields.
        assert all(
            secret not in value
            for value in record.__dict__.values()
            if isinstance(value, str)
        )
        assert secret not in formatter.format(record)


@pytest.mark.parametrize("redacted", [False, True], ids=["original", "redacted"])
@pytest.mark.parametrize("rich_first", [False, True], ids=["files-first", "rich-first"])
def test_exception_diagnostics_survive_all_handlers(redacted, rich_first):
    """One shared record keeps its traceback exactly once in every output."""
    access_key, secret_key = "example-access-key", "example-secret-key"
    outputs = {name: StringIO() for name in ("text", "json", "rich")}
    handlers = []
    for name, formatter in (
        ("text", OpenHoundTextFormatter()),
        ("json", OpenHoundJSONFormatter()),
        ("rich", OpenHoundRichFormatter()),
    ):
        handler = (
            RichHandler(
                console=Console(file=outputs[name], width=240, color_system=None),
                show_time=False,
                show_path=False,
                markup=True,
                rich_tracebacks=True,
            )
            if name == "rich"
            else logging.StreamHandler(outputs[name])
        )
        handler.setFormatter(formatter)
        handlers.append(handler)
    if rich_first:
        handlers.reverse()
    logger = logging.getLogger("exception-diagnostics-test")
    previous = logger.handlers[:], logger.level, logger.propagate
    logger.handlers = handlers
    logger.setLevel(logging.ERROR)
    logger.propagate = False
    try:
        with redact_log_values((access_key, secret_key)) if redacted else nullcontext():
            try:
                raise RuntimeError(f"AccessDenied for {access_key}: {secret_key}")
            except RuntimeError:
                logger.exception("Collection failed")
        for name, output in outputs.items():
            rendered = output.getvalue()
            if name == "json":
                document = json.loads(rendered)
                assert document["message"] == "Collection failed"
                rendered = document["exception"]
            assert rendered.count("RuntimeError: AccessDenied") == 1
            assert rendered.count("Traceback (most recent call last)") == 1
            if redacted:
                assert access_key not in rendered
                assert secret_key not in rendered
                assert "[REDACTED]" in rendered
            else:
                assert access_key in rendered
                assert secret_key in rendered
    finally:
        logger.handlers, logger.level, logger.propagate = previous
        for handler in handlers:
            handler.close()


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


@pytest.mark.parametrize("rich", [False, True], ids=["stdout", "rich-console"])
def test_parent_worker_routing_deduplicates_shared_console_destinations(
    configured_logger, rich
):
    output = StringIO()
    handlers = [
        RichHandler(console=Console(file=output), show_time=False, show_path=False)
        if rich
        else logging.StreamHandler(output)
        for _ in range(2)
    ]
    configured_logger.root_logger.addHandler(handlers[0])
    configured_logger.dlt_logger.addHandler(handlers[1])
    try:
        record = logging.LogRecord(
            "dlt", logging.ERROR, __file__, 1, "one-delivery", (), None
        )
        _ParentWorkerHandler(configured_logger).handle(record)
        assert output.getvalue().count("one-delivery") == 1
    finally:
        configured_logger.root_logger.removeHandler(handlers[0])
        configured_logger.dlt_logger.removeHandler(handlers[1])
        for handler in handlers:
            handler.close()


def test_root_and_dlt_loggers_share_single_file_handler(configured_logger):
    """The root and dlt loggers writing to openhound.log must share one handler
    instance so the file is only opened once, which is required for rotation on
    Windows where an open handle blocks renaming the file."""
    root_file_handlers = [
        handler
        for handler in configured_logger.root_logger.handlers
        if isinstance(handler, RotatingFileHandler)
    ]
    dlt_file_handlers = [
        handler
        for handler in configured_logger.dlt_logger.handlers
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


WORKER_ACCESS = "worker-example-access-key"
WORKER_SECRET = "worker-example-secret-key"


def _spawned_log_collection(collector, job_id, params, credentials, managed, directory):
    """Run the real job/configuration bridge; replace only collection/network IO."""
    from dlt.common import logger as dlt_logger

    from openhound.core.logging import logger_override
    from openhound.scheduler import service

    def collect(*args):
        # DLT runtime initialization can configure its logger, as can each of
        # OpenHound's collect/preprocess/convert stages. All must keep forwarding.
        dlt.pipeline(
            pipeline_name="worker_logs", pipelines_dir=str(directory / "pipelines")
        )
        logger_override.setup()
        CustomLogger("unused.log", base_path="/must/not/be/created").setup()
        logger_override.set_handler("aws")
        logger_override.set_handler("aws")
        loggers = [logging.getLogger(), *logging.Logger.manager.loggerDict.values()]
        assert not any(
            isinstance(handler, logging.FileHandler)
            for logger in loggers
            if isinstance(logger, logging.Logger)
            for handler in logger.handlers
        ), "scheduler workers must not own file handlers"
        logging.getLogger("openhound.core.collect").info(
            "forwarded-core %s",
            WORKER_ACCESS,
            extra={
                "details": {"secret": WORKER_SECRET, "values": [False, 0, None]},
                "path": Path("structured-path"),
            },
        )
        logging.getLogger("openhound_aws.auth").warning(
            "forwarded-extension %s",
            WORKER_SECRET,
            extra={
                "job_id": "explicit-job",
                "collector_extension": "explicit-collector",
            },
        )
        dlt_logger.info(
            "forwarded-dlt %s", WORKER_SECRET, extra={"metrics": {"rows": 7}}
        )
        try:
            raise RuntimeError(f"diagnostic {WORKER_SECRET}")
        except RuntimeError:
            logging.getLogger("openhound.core.collect").exception("forwarded-exception")
        logging.getLogger("botocore.worker_test").debug("forbidden-sdk-response")
        if params.get("test", {}).get("fail"):
            raise RuntimeError(f"worker failure {WORKER_SECRET}")
        return service.Result({}, job_id)

    service._collect_in_worker = collect
    # Network IO is out of scope for this logging-only worker.
    service.validate_aws_profile = lambda profile: None
    return service._subprocess_collect(
        collector, job_id, params, credentials, managed, directory
    )


def _spawned_log_then_die():
    logging.getLogger("openhound.core.collect").warning("before-abrupt-death")
    os._exit(17)


def _spawned_busy_logger():
    logger = logging.getLogger("openhound.core.collect")
    logger.warning("before-abrupt-death")
    while True:
        logger.info("worker-writing", extra={"payload": "x" * 65536})


@pytest.fixture
def logging_scheduler(configured_logger, monkeypatch):
    from openhound.core import logging as openhound_logging
    from openhound.scheduler.service import Service

    monkeypatch.setattr(openhound_logging, "logger_override", configured_logger)
    scheduler = Service("https://bhe.example.test", "token", "id", "aws", managed=False)
    monkeypatch.setattr(scheduler.client, "start_collector_job", lambda job_id: None)
    monkeypatch.setattr(
        scheduler.client, "end_managed_job", lambda *args, **kwargs: None
    )
    try:
        yield scheduler
    finally:
        scheduler.executor.shutdown(wait=True, cancel_futures=True)
        scheduler._cleanup_aws_profile_workspace()
        scheduler._worker_logging.close()


def test_real_spawned_worker_routes_redacted_records_and_survives_replacement(
    logging_scheduler, configured_logger, tmp_path, monkeypatch
):
    from openhound.scheduler.aws_profile import AWSAccessKeyCredentials
    from openhound.scheduler.configuration import ManagedConfigurationError

    scheduler = logging_scheduler
    records = []

    class ParentOnlyHandler(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = ParentOnlyHandler()
    logging.getLogger().addHandler(handler)
    listener_thread = scheduler._worker_logging.listener._thread
    manager_process = scheduler._worker_logging.manager._process
    try:
        for index in range(3):
            if index == 2:
                scheduler._reset_executor()
            executor = scheduler.executor
            submit = executor.submit
            with monkeypatch.context() as patch:
                patch.setattr(
                    executor,
                    "submit",
                    lambda function, *args, submit=submit: submit(
                        _spawned_log_collection, *args
                    ),
                )
                scheduler.run_claimed_job(
                    f"job-{index}",
                    {"test": {"fail": index == 1}},
                    AWSAccessKeyCredentials(WORKER_ACCESS, WORKER_SECRET),
                )
            if index == 1:
                with pytest.raises(ManagedConfigurationError) as error:
                    scheduler.future.result(timeout=30)
                assert WORKER_SECRET not in str(error.value)
            else:
                assert scheduler.future.result(timeout=30).job_id == f"job-{index}"
            scheduler._handle_completed_job(scheduler.future)
        scheduler._shutdown()
        assert not listener_thread.is_alive()
        assert not manager_process.is_alive()
        assert scheduler._worker_logging.listener is None
        assert scheduler._worker_logging.manager is None
        # Closing twice is safe, including after Service shutdown.
        scheduler._worker_logging.close()

        core = [
            record
            for record in records
            if record.getMessage().startswith("forwarded-core")
        ]
        extension = [
            record
            for record in records
            if record.getMessage().startswith("forwarded-extension")
        ]
        dlt_records = [
            record
            for record in records
            if record.getMessage().startswith("forwarded-dlt")
        ]
        exceptions = [
            record for record in records if record.getMessage() == "forwarded-exception"
        ]
        assert len(core) == len(extension) == len(dlt_records) == len(exceptions) == 3
        assert {record.job_id for record in core} == {"job-0", "job-1", "job-2"}
        assert all(
            record.collector_extension == "aws"
            for record in core + dlt_records + exceptions
        )
        assert all(
            record.job_id == "explicit-job"
            and record.collector_extension == "explicit-collector"
            for record in extension
        )
        assert all(
            record.name == "openhound_aws.auth" and record.levelno == logging.WARNING
            for record in extension
        )
        assert all(
            record.details == {"secret": "[REDACTED]", "values": [False, 0, None]}
            and record.path == "structured-path"
            for record in core
        )
        assert all(record.metrics == {"rows": 7} for record in dlt_records)
        assert all(
            "RuntimeError: diagnostic [REDACTED]" in record.exc_text
            and "collect" in record.exc_text
            for record in exceptions
        )
        assert any(
            "worker failure [REDACTED]" in (record.exc_text or "") for record in records
        )
        assert "forbidden-sdk-response" not in repr(
            [record.__dict__ for record in records]
        )
        for secret in (WORKER_ACCESS, WORKER_SECRET):
            assert secret not in repr([record.__dict__ for record in records])
            assert secret not in (tmp_path / "openhound.log").read_text()
            assert secret not in (tmp_path / "ext_aws.log").read_text()
        main = [
            json.loads(line)
            for line in (tmp_path / "openhound.log").read_text().splitlines()
        ]
        assert sum(row["message"].startswith("forwarded-dlt") for row in main) == 3
        extension_file = [
            json.loads(line)
            for line in (tmp_path / "ext_aws.log").read_text().splitlines()
        ]
        assert (
            sum(row["message"].startswith("forwarded-dlt") for row in extension_file)
            == 3
        )
    finally:
        logging.getLogger().removeHandler(handler)


@pytest.mark.parametrize("claim_lost", [False, True], ids=["broken-pool", "claim-loss"])
def test_worker_logging_abrupt_termination_and_shutdown_do_not_hang(
    logging_scheduler, claim_lost, tmp_path
):
    scheduler = logging_scheduler
    listener_thread = scheduler._worker_logging.listener._thread
    if claim_lost:
        ready = threading.Event()

        class ReadyHandler(logging.Handler):
            def emit(self, record):
                if record.getMessage() == "worker-writing":
                    ready.set()

        handler = ReadyHandler()
        logging.getLogger().addHandler(handler)
        try:
            future = scheduler.executor.submit(_spawned_busy_logger)
            scheduler.future = future
            assert ready.wait(30), "worker did not begin forwarding records"
            # Kill a live producer while it is continuously forwarding records,
            # rather than only exercising orderly queue submissions before exit.
            scheduler._abort_managed_job("job")
            with pytest.raises(BrokenProcessPool):
                future.result(timeout=30)
        finally:
            logging.getLogger().removeHandler(handler)
    else:
        scheduler.future = scheduler.executor.submit(_spawned_log_then_die)
        with pytest.raises(BrokenProcessPool):
            scheduler.future.result(timeout=30)
        scheduler._reset_executor()
        scheduler.future = None
    # A replacement initialized through either reset path still forwards.
    future = scheduler.executor.submit(_spawned_log_then_die)
    with pytest.raises(BrokenProcessPool):
        future.result(timeout=30)
    scheduler.future = None
    finished = threading.Event()

    def shutdown():
        scheduler._shutdown()
        finished.set()

    thread = threading.Thread(target=shutdown, daemon=True)
    thread.start()
    assert finished.wait(10), "shutdown hung after abrupt worker termination"
    thread.join(timeout=1)
    assert not listener_thread.is_alive()
    main = [
        json.loads(line)
        for line in (tmp_path / "openhound.log").read_text().splitlines()
    ]
    assert sum(row["message"] == "before-abrupt-death" for row in main) == 2
