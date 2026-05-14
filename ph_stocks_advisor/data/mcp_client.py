"""
Synchronous client for the PH Stocks Advisor MCP server.

The official ``mcp`` Python SDK is async-only. The advisor codebase calls its
data tools synchronously (e.g. inside LangGraph nodes that run in a thread
pool), so this module wraps an async ``ClientSession`` running on a
dedicated background event-loop thread, exposing a simple sync ``call``
method.

A single persistent session is kept open for the lifetime of the process to
avoid the MCP initialisation handshake overhead on every call.

Single Responsibility: protocol/transport details only — knows nothing about
which tools exist or what they return. Callers (``data/tools.py``) are
responsible for deserialising the structured-content payload back into the
appropriate Pydantic model.
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import os
import threading
from typing import Any

from ph_stocks_advisor.data.clients.dragonfi import SymbolNotFoundError

logger = logging.getLogger(__name__)


# Marker prefix the server attaches to ``SymbolNotFoundError`` messages so we
# can re-raise the original exception type on the client side.
_SYMBOL_NOT_FOUND_PREFIX = "SymbolNotFoundError: "


def _env_float(name: str, default: float) -> float:
    """Read a positive float from the environment, falling back on errors."""
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("Invalid float for %s=%r, using default %s", name, raw, default)
        return default
    return value if value > 0 else default


def _make_native_thread(*, target, name: str) -> threading.Thread:
    """Build a daemon thread that bypasses gevent monkey-patching.

    Under a gevent worker the global ``threading.Thread`` is a cooperative
    greenlet, which deadlocks when running an asyncio event loop with a
    long-lived ``run_forever``. We force a real OS thread when the
    unpatched class is reachable; otherwise fall back to the patched one.
    """
    native_cls = threading.Thread
    try:
        from gevent.monkey import get_original  # type: ignore[import-not-found]

        native_cls = get_original("threading", "Thread")
    except Exception as exc:  # pragma: no cover - gevent not installed / not patched
        logger.debug("gevent get_original unavailable, using stdlib Thread: %s", exc)
    thread = native_cls(target=target, name=name, daemon=True)
    return thread


class MCPClientError(RuntimeError):
    """Raised when an MCP tool call fails for a non-business reason."""


class _SyncMCPClient:
    """Thread-safe synchronous façade over an async MCP ``ClientSession``.

    The async session and its transport are owned by a daemon background
    thread running its own event loop. ``call`` blocks the caller until the
    coroutine completes on that loop.
    """

    def __init__(self, url: str, *, request_timeout: float | None = None, connect_timeout: float | None = None) -> None:
        self._url = url
        self._request_timeout = request_timeout if request_timeout is not None else _env_float("MCP_REQUEST_TIMEOUT", 60.0)
        self._connect_timeout = connect_timeout if connect_timeout is not None else _env_float("MCP_CONNECT_TIMEOUT", 60.0)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._session = None  # type: ignore[var-annotated]
        self._stack = None  # type: ignore[var-annotated]
        self._ready = threading.Event()
        self._start_error: BaseException | None = None
        self._lock = threading.Lock()
        self._closed = False
        self._owner_pid = os.getpid()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def _reset_locked(self) -> None:
        """Reset thread/loop/session state. Caller must hold ``self._lock``."""
        self._loop = None
        self._thread = None
        self._session = None
        self._stack = None
        self._ready = threading.Event()
        self._start_error = None
        self._closed = False
        self._owner_pid = os.getpid()

    def _ensure_started(self) -> None:
        if self._session is not None and self._owner_pid == os.getpid():
            return
        with self._lock:
            # Detect fork (e.g. Celery prefork worker): the inherited loop
            # thread does not survive ``os.fork()`` so any prior session
            # state in this process is dead. Discard and rebuild.
            if self._owner_pid != os.getpid():
                logger.info(
                    "MCP client detected fork (owner_pid=%s, current_pid=%s); rebuilding session",
                    self._owner_pid,
                    os.getpid(),
                )
                self._reset_locked()
            if self._session is not None:
                return
            if self._thread is None:
                self._loop = asyncio.new_event_loop()
                self._ready = threading.Event()
                self._start_error = None
                self._thread = _make_native_thread(
                    target=self._run_loop,
                    name=f"mcp-client-{self._url}",
                )
                self._thread.start()
            if not self._ready.wait(timeout=self._connect_timeout):
                # Tear the half-started state down so the NEXT call retries
                # cleanly instead of waiting on the same already-set event
                # forever.
                self._reset_locked()
                raise MCPClientError(
                    f"Timed out after {self._connect_timeout}s waiting for MCP "
                    f"session to initialise against {self._url}"
                )
            if self._start_error is not None:
                err = self._start_error
                self._reset_locked()
                raise MCPClientError(f"Failed to connect to MCP server at {self._url}: {err}")
            if self._session is None:
                self._reset_locked()
                raise MCPClientError(f"MCP session was not established against {self._url} (no error reported)")
            atexit.register(self.close)

    def _run_loop(self) -> None:
        assert self._loop is not None
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._open_session())
            self._loop.run_forever()
        except BaseException as exc:  # pragma: no cover - thread-level guard
            self._start_error = exc
            self._ready.set()

    async def _open_session(self) -> None:
        from contextlib import AsyncExitStack

        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        try:
            self._stack = AsyncExitStack()
            read, write, _ = await self._stack.enter_async_context(streamablehttp_client(self._url))
            session = await self._stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            self._session = session
            logger.info("MCP client session initialised against %s", self._url)
        except BaseException as exc:
            self._start_error = exc
            if self._stack is not None:
                await self._stack.aclose()
                self._stack = None
        finally:
            self._ready.set()

    def close(self) -> None:
        if self._closed or self._loop is None:
            return
        self._closed = True

        async def _shutdown() -> None:
            if self._stack is not None:
                try:
                    await self._stack.aclose()
                except Exception as exc:  # pragma: no cover - best-effort cleanup
                    logger.debug("Error closing MCP session: %s", exc)

        try:
            future = asyncio.run_coroutine_threadsafe(_shutdown(), self._loop)
            future.result(timeout=5)
        except Exception as exc:  # pragma: no cover
            logger.debug("MCP shutdown raised: %s", exc)
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def call(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Invoke an MCP tool and return its structured payload.

        Returns the tool's structured content (``dict`` for Pydantic-model
        tools, ``str`` / primitive for scalar tools). Raises
        :class:`SymbolNotFoundError` when the server reports a missing
        symbol, or :class:`MCPClientError` for any other failure.

        Transparently rebuilds the underlying session once if the previous
        one was torn down (e.g. server restart, transport error).
        """
        last_error: BaseException | None = None
        for attempt in range(2):
            self._ensure_started()
            if self._session is None or self._loop is None:
                raise MCPClientError(f"MCP client is not ready (url={self._url})")

            try:
                future = asyncio.run_coroutine_threadsafe(
                    self._call_async(tool_name, arguments),
                    self._loop,
                )
                return future.result(timeout=self._request_timeout)
            except SymbolNotFoundError:
                raise
            except MCPClientError:
                raise
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "MCP tool %r failed on attempt %d (%s); rebuilding session",
                    tool_name,
                    attempt + 1,
                    exc,
                )
                with self._lock:
                    self._reset_locked()
        raise MCPClientError(f"MCP tool {tool_name!r} failed after retry: {last_error}")

    async def _call_async(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        assert self._session is not None
        result = await self._session.call_tool(tool_name, arguments)

        if result.isError:
            message = _extract_text(result)
            if message.startswith(_SYMBOL_NOT_FOUND_PREFIX):
                raise SymbolNotFoundError(message[len(_SYMBOL_NOT_FOUND_PREFIX) :])
            raise MCPClientError(f"MCP tool {tool_name!r} failed: {message}")

        # FastMCP returns Pydantic models / dicts as ``structuredContent``.
        structured = getattr(result, "structuredContent", None)
        if structured is not None:
            # FastMCP wraps non-object return types under a ``result`` key.
            if isinstance(structured, dict) and set(structured.keys()) == {"result"}:
                return structured["result"]
            return structured

        # Fallback: text content (scalar tools).
        return _extract_text(result)


def _extract_text(result: Any) -> str:
    parts: list[str] = []
    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Module-level singleton accessor
# ---------------------------------------------------------------------------

_client: _SyncMCPClient | None = None
_client_lock = threading.Lock()


class MCPNotConfiguredError(RuntimeError):
    """Raised when ``MCP_SERVER_URL`` is not configured.

    The advisor depends entirely on the MCP server for market data — there
    is no in-process fallback. A missing ``MCP_SERVER_URL`` is therefore a
    hard configuration error, not a recoverable condition.
    """


def get_client(url: str | None = None) -> _SyncMCPClient:
    """Return the shared MCP client.

    The URL is read from ``settings.mcp_server_url`` when not supplied.
    Raises :class:`MCPNotConfiguredError` when the URL is empty so callers
    fail fast with a clear, actionable message instead of silently doing
    the wrong thing.
    """
    if url is None:
        from ph_stocks_advisor.infra.config import get_settings

        url = get_settings().mcp_server_url

    if not url:
        raise MCPNotConfiguredError(
            "MCP_SERVER_URL is not set. The advisor requires the PH Stocks "
            "Advisor MCP server for all market-data calls. Start the MCP "
            "service (e.g. via docker compose) and set "
            "MCP_SERVER_URL=http://mcp:8000/mcp/ (or the appropriate URL)."
        )

    global _client
    with _client_lock:
        # Detect fork: a singleton inherited from a parent process has a
        # dead loop thread and must be discarded.
        if _client is not None and _client._owner_pid != os.getpid():
            logger.info(
                "Discarding MCP client inherited from parent process (owner_pid=%s, current_pid=%s)",
                _client._owner_pid,
                os.getpid(),
            )
            _client = None
        if _client is None or _client._url != url:
            if _client is not None:
                _client.close()
            _client = _SyncMCPClient(url)
        return _client


def reset_client() -> None:
    """Reset the singleton — primarily for tests."""
    global _client
    with _client_lock:
        if _client is not None:
            _client.close()
        _client = None
