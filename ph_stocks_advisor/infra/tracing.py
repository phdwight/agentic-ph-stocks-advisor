"""
Langfuse tracing integration.

Provides :func:`build_langfuse_config` which returns a LangChain
``RunnableConfig`` with the Langfuse callback handler attached and
trace-level attributes (``user_id``, ``session_id``, ``tags``,
``run_name``, ``metadata``) populated using the conventions documented
at https://langfuse.com/integrations/frameworks/langchain.

Tracing is fully optional and isolated: when the ``langfuse`` package
is not installed, the credentials are missing, or
``LANGFUSE_TRACING_ENABLED`` is set to a falsy value, the returned
config is empty and the application runs without tracing.

Single Responsibility: this module is the only place that knows about
Langfuse — agents and graphs depend on this abstraction, not on the
SDK directly (Dependency Inversion).
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


_FALSY = {"0", "false", "no", "off", ""}


def _tracing_enabled() -> bool:
    if not os.getenv("LANGFUSE_PUBLIC_KEY") or not os.getenv("LANGFUSE_SECRET_KEY"):
        return False
    flag = os.getenv("LANGFUSE_TRACING_ENABLED", "true").strip().lower()
    return flag not in _FALSY


def _get_callback_handler() -> Any | None:
    """Return a Langfuse LangChain CallbackHandler, or ``None`` if disabled."""
    if not _tracing_enabled():
        return None
    try:
        from langfuse.langchain import CallbackHandler  # type: ignore[import-not-found]
    except ImportError:
        logger.debug("langfuse not installed; tracing disabled.")
        return None
    try:
        return CallbackHandler()
    except Exception:  # pragma: no cover — defensive: never break the run
        logger.exception("Failed to initialize Langfuse callback handler.")
        return None


def build_langfuse_config(
    *,
    run_name: str,
    user_id: str | None = None,
    session_id: str | None = None,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a LangChain ``RunnableConfig`` with Langfuse tracing attached.

    Returns an empty dict when tracing is disabled, so callers can do::

        graph.invoke(state, config=build_langfuse_config(...))

    unconditionally.

    Parameters
    ----------
    run_name : str
        Descriptive trace name (e.g. ``"stock-analysis"``). Avoid
        generic names like ``"trace-1"``.
    user_id : str | None
        Authenticated user identifier. Surfaces in the Langfuse "Users"
        view and enables per-user filtering and cost attribution.
    session_id : str | None
        Logical session identifier. Groups related traces (e.g. all
        traces produced by one Celery task or one chat turn).
    tags : list[str] | None
        Free-form labels for filtering in the dashboard.
    metadata : dict | None
        Arbitrary additional trace metadata (e.g. ``{"symbol": "TEL"}``).
    """
    handler = _get_callback_handler()
    if handler is None:
        return {}

    md: dict[str, Any] = dict(metadata or {})
    if user_id:
        md["langfuse_user_id"] = user_id
    if session_id:
        md["langfuse_session_id"] = session_id
    if tags:
        md["langfuse_tags"] = list(tags)

    config: dict[str, Any] = {
        "callbacks": [handler],
        "run_name": run_name,
    }
    if md:
        config["metadata"] = md
    return config


def flush_langfuse() -> None:
    """Flush any buffered Langfuse spans.

    Langfuse v3+ batches spans via OpenTelemetry, so short-lived
    processes (e.g. a single Celery task) may exit before traces are
    exported. Call this at the end of a unit of work to force the
    export.
    """
    if not _tracing_enabled():
        return
    try:
        from langfuse import get_client  # type: ignore[import-not-found]
    except ImportError:
        return
    try:
        get_client().flush()
    except Exception:  # pragma: no cover — defensive
        logger.exception("Failed to flush Langfuse client.")
