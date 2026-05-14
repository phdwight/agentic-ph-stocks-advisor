"""
Tests for the unified logging configuration.

These tests verify behaviour, not implementation: do log lines come out
in the expected shape, do third-party loggers stop double-emitting, and
does Uvicorn pick up our format when FastMCP boots it?
"""

from __future__ import annotations

import io
import logging
import re

import pytest

from ph_stocks_advisor.infra.logging import (
    LOG_FORMAT_CELERY,
    LOG_FORMAT_CELERY_TASK,
    LOG_FORMAT_GUNICORN_ACCESS,
    configure_logging,
)


@pytest.fixture(autouse=True)
def _reset_logging():
    """Snapshot and restore root logger state around every test so we
    don't leak ``configure_logging`` side-effects into the rest of the
    suite."""
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    yield
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)


def _capture_root_output() -> tuple[io.StringIO, logging.Handler]:
    """Replace the root handler with a StringIO sink we can read."""
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    root = logging.getLogger()
    handler.setFormatter(root.handlers[0].formatter)
    root.handlers[:] = [handler]
    return buf, handler


def test_configure_logging_emits_unified_format() -> None:
    configure_logging("INFO")
    buf, _ = _capture_root_output()

    logging.getLogger("ph_stocks_advisor.demo").info("hello world")

    line = buf.getvalue().strip()
    # Format: "<asctime> [INFO   ] <logger> :: <message>"
    assert re.match(
        r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} \[INFO\s+\] ph_stocks_advisor\.demo :: hello world$",
        line,
    ), f"Unexpected line shape: {line!r}"


def test_configure_logging_resets_existing_handlers() -> None:
    """A second call must not stack a duplicate handler on the root."""
    configure_logging("INFO")
    first_count = len(logging.getLogger().handlers)

    configure_logging("INFO")
    second_count = len(logging.getLogger().handlers)

    assert first_count == second_count == 1


def test_third_party_loggers_do_not_double_emit() -> None:
    """Uvicorn/Gunicorn loggers must propagate, not own, their handlers
    so messages appear exactly once on the root sink."""
    configure_logging("INFO")
    buf, _ = _capture_root_output()

    for noisy in ("uvicorn", "uvicorn.access", "uvicorn.error", "gunicorn.error", "gunicorn.access"):
        lg = logging.getLogger(noisy)
        assert lg.handlers == [], f"{noisy} should have no handlers of its own"
        assert lg.propagate is True, f"{noisy} should propagate to root"

    logging.getLogger("uvicorn.access").info("GET /healthz 200")
    output = buf.getvalue()
    assert output.count("GET /healthz 200") == 1


def test_log_level_honours_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    configure_logging()
    buf, _ = _capture_root_output()

    log = logging.getLogger("ph_stocks_advisor.demo")
    log.info("should be filtered")
    log.warning("should pass")

    output = buf.getvalue()
    assert "should be filtered" not in output
    assert "should pass" in output


def test_uvicorn_logging_config_is_patched() -> None:
    """FastMCP boots Uvicorn with the module-level default config; we
    must replace it so the resulting access lines use our format."""
    import uvicorn.config as uvc

    configure_logging("INFO")
    cfg = uvc.LOGGING_CONFIG  # type: ignore[attr-defined]

    assert isinstance(cfg, dict)
    assert "uvicorn.access" in cfg["loggers"]
    fmt = cfg["formatters"]["default"]["format"]
    assert "%(asctime)s" in fmt
    assert "%(levelname)" in fmt
    assert "%(name)s" in fmt


def test_celery_format_strings_carry_required_fields() -> None:
    """The shared Celery format strings must include every field the
    Celery worker injects so log lines are not garbled."""
    assert "%(processName)s" in LOG_FORMAT_CELERY
    assert "%(asctime)s" in LOG_FORMAT_CELERY
    assert "%(levelname)" in LOG_FORMAT_CELERY

    assert "%(task_name)s" in LOG_FORMAT_CELERY_TASK
    assert "%(task_id)s" in LOG_FORMAT_CELERY_TASK


def test_gunicorn_access_format_includes_request_fields() -> None:
    for token in ("%(t)s", "%(h)s", "%(r)s", "%(s)s", "gunicorn.access"):
        assert token in LOG_FORMAT_GUNICORN_ACCESS


def test_celery_setup_logging_handler_prevents_duplicate_emission() -> None:
    """Regression test for the bug that printed every worker line twice.

    The handler must (a) configure logging itself and (b) leave the
    ``ph_stocks_advisor`` logger without its own handler, so messages
    are emitted exactly once via root-logger propagation.
    """
    from ph_stocks_advisor.web.celery_app import _configure_celery_logging

    _configure_celery_logging(loglevel=logging.INFO)
    buf, _ = _capture_root_output()

    app_logger = logging.getLogger("ph_stocks_advisor")
    assert app_logger.handlers == [], "app logger must rely on root, not own handlers"

    logging.getLogger("ph_stocks_advisor.task.demo").info("starting analysis for SM")
    output = buf.getvalue()
    assert output.count("starting analysis for SM") == 1
