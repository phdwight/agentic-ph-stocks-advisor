"""
Unified logging configuration shared by every Python entry point
(MCP server, Celery worker, Flask/Gunicorn web app).

Every service produces log lines in the same shape so multi-container
``docker compose logs`` output is readable and machine-parseable:

    2026-05-14 06:52:48,572 [INFO   ] ph_stocks_mcp.server :: MCP tool called: get_stock_price(symbol='TEL')

The format is intentionally close to Celery's default so the existing
``[2026-05-14 06:52:48,572: INFO/ForkPoolWorker-3] ...`` lines can be
re-aligned by setting Celery's ``worker_log_format`` to
``LOG_FORMAT_CELERY``.

Single Responsibility: this module owns formatting only — it does not
decide *what* to log.
"""

from __future__ import annotations

import logging
import os
from typing import Any

# ---------------------------------------------------------------------------
# Canonical formats
# ---------------------------------------------------------------------------

#: Format used by every plain ``logging.getLogger(__name__)`` call.
LOG_FORMAT: str = "%(asctime)s [%(levelname)-7s] %(name)s :: %(message)s"

#: Celery worker (non-task) log lines — substitutes ``%(processName)s``
#: for ``%(name)s`` since Celery's bookkeeping logger is more useful as a
#: process tag.
LOG_FORMAT_CELERY: str = "%(asctime)s [%(levelname)-7s] %(processName)s :: %(message)s"

#: Celery per-task log lines — adds ``task_name[task_id]`` so each line
#: can be traced back to the originating job.
LOG_FORMAT_CELERY_TASK: str = (
    "%(asctime)s [%(levelname)-7s] %(processName)s :: %(task_name)s[%(task_id)s] :: %(message)s"
)

#: Gunicorn access log — passed via ``--access-logformat`` so request
#: lines align with the rest of the stack.
LOG_FORMAT_GUNICORN_ACCESS: str = '%(t)s [INFO   ] gunicorn.access :: %(h)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s"'

#: Default datefmt — matches Celery's built-in default so timestamps
#: line up across services without bespoke configuration.
DATE_FORMAT: str | None = None  # ``None`` keeps logging's ISO-ish default with milliseconds.


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def configure_logging(level: int | str | None = None) -> None:
    """Install the unified format on the root logger.

    Calling this function:

    1. Resets the root logger so any earlier ``basicConfig()`` call is
       superseded (``force=True``).
    2. Strips per-logger handlers from common third-party loggers
       (``uvicorn.*``) so their messages flow through the root handler
       and pick up our format instead of their built-in one.
    3. Patches ``uvicorn.config.LOGGING_CONFIG`` so that when FastMCP
       boots Uvicorn, the ``INFO:     127.0.0.1 - "POST /mcp/"`` access
       lines are emitted in our format too.
    """
    resolved_level = _resolve_level(level)

    logging.basicConfig(
        format=LOG_FORMAT,
        datefmt=DATE_FORMAT,
        level=resolved_level,
        force=True,
    )

    # Hand control of these loggers back to the root logger so they
    # adopt our format. Without this they keep their library defaults.
    for noisy in ("uvicorn", "uvicorn.error", "uvicorn.access", "gunicorn.error", "gunicorn.access"):
        lg = logging.getLogger(noisy)
        lg.handlers.clear()
        lg.propagate = True

    _patch_uvicorn_logging_config(resolved_level)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_level(level: int | str | None) -> int:
    """Resolve a log level from arg → ``LOG_LEVEL`` env → ``INFO``."""
    if level is None:
        level = os.getenv("LOG_LEVEL", "INFO")
    if isinstance(level, str):
        return logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
    return level


def _patch_uvicorn_logging_config(level: int) -> None:
    """Replace Uvicorn's default ``LOGGING_CONFIG`` with our format.

    FastMCP does not expose ``log_config``; instead Uvicorn picks up the
    module-level default. Mutating it before ``mcp.run()`` is the
    least-invasive way to keep MCP request/response lines consistent
    with the rest of the stack.
    """
    try:
        import uvicorn.config as _uvc  # local import — uvicorn is optional
    except ImportError:  # pragma: no cover — uvicorn is a transitive dep of FastMCP
        return

    level_name = logging.getLevelName(level)
    _uvc.LOGGING_CONFIG = {  # type: ignore[attr-defined]
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {"format": LOG_FORMAT, "datefmt": DATE_FORMAT},
        },
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "formatter": "default",
                "stream": "ext://sys.stderr",
            },
        },
        "loggers": {
            "uvicorn": {"handlers": ["default"], "level": level_name, "propagate": False},
            "uvicorn.error": {"handlers": ["default"], "level": level_name, "propagate": False},
            "uvicorn.access": {"handlers": ["default"], "level": level_name, "propagate": False},
        },
    }


__all__: list[Any] = [
    "configure_logging",
    "LOG_FORMAT",
    "LOG_FORMAT_CELERY",
    "LOG_FORMAT_CELERY_TASK",
    "LOG_FORMAT_GUNICORN_ACCESS",
]
