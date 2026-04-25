"""
Tests for the PH Stocks Advisor MCP server and the dispatching façade in
``ph_stocks_advisor.data.tools``.

These tests focus on **behaviour**:

1. The MCP server exposes the expected tools and returns the same data the
   in-process service would have returned.
2. The façade dispatches through the MCP client when ``MCP_SERVER_URL`` is
   set, and re-raises ``SymbolNotFoundError`` for unknown symbols across the
   network boundary.
3. The façade falls back to the in-process implementation when no MCP URL is
   configured, so existing tests and the standalone CLI keep working.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from ph_stocks_advisor.data import tools
from ph_stocks_advisor.data.clients.dragonfi import SymbolNotFoundError
from ph_stocks_advisor.data.mcp_client import reset_client
from ph_stocks_advisor.data.models import (
    ControversyInfo,
    DividendInfo,
    FairValueEstimate,
    PriceMovement,
    SentimentInfo,
    StockPrice,
)
from ph_stocks_mcp.server import build_server

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _validate_stub(symbol: str) -> str:
    upper = symbol.upper()
    if upper == "TEL":
        return upper
    raise SymbolNotFoundError(f"Symbol '{symbol}' is not listed.")


@pytest.fixture
def mcp_in_memory_session():
    """Yield a callable that runs an async test against an in-memory MCP server.

    Uses the official ``mcp`` SDK's in-memory transport so we don't need a
    real network port. The fixture also patches the underlying domain
    services with deterministic stubs so we can assert on returned values
    without hitting real APIs.
    """
    from mcp.shared.memory import create_connected_server_and_client_session

    sample_price = StockPrice(symbol="TEL", current_price=1250.0)
    sample_dividend = DividendInfo(symbol="TEL", dividend_yield=0.06)
    sample_movement = PriceMovement(symbol="TEL", year_change_pct=12.5)
    sample_fair = FairValueEstimate(symbol="TEL", current_price=1250.0, pe_ratio=8.0)
    sample_controversy = ControversyInfo(symbol="TEL", risk_factors=["test"])
    sample_sentiment = SentimentInfo(symbol="TEL", sector="Telecoms")

    patches = [
        patch("ph_stocks_mcp.server.fetch_stock_price", return_value=sample_price),
        patch("ph_stocks_mcp.server.fetch_dividend_info", return_value=sample_dividend),
        patch("ph_stocks_mcp.server.fetch_price_movement", return_value=sample_movement),
        patch("ph_stocks_mcp.server.fetch_fair_value", return_value=sample_fair),
        patch("ph_stocks_mcp.server.fetch_controversy_info", return_value=sample_controversy),
        patch("ph_stocks_mcp.server.fetch_sentiment_info", return_value=sample_sentiment),
        patch("ph_stocks_mcp.server.validate_pse_symbol", side_effect=_validate_stub),
    ]
    for p in patches:
        p.start()

    server = build_server()

    async def _do(test):
        async with create_connected_server_and_client_session(server._mcp_server) as session:  # type: ignore[attr-defined]
            await test(session)

    yield _do

    for p in patches:
        p.stop()


# ---------------------------------------------------------------------------
# Server-side behaviour
# ---------------------------------------------------------------------------


def test_mcp_server_lists_all_advisor_tools(mcp_in_memory_session):
    expected = {
        "validate_symbol",
        "get_stock_price",
        "get_dividend_info",
        "get_price_movement",
        "get_fair_value",
        "get_controversy_info",
        "get_sentiment_info",
    }

    async def _test(session):
        listed = await session.list_tools()
        names = {t.name for t in listed.tools}
        assert expected.issubset(names)

    _run(mcp_in_memory_session(_test))


def test_mcp_server_returns_stock_price_payload(mcp_in_memory_session):
    async def _test(session):
        result = await session.call_tool("get_stock_price", {"symbol": "TEL"})
        assert result.isError is False
        data = result.structuredContent
        assert data["symbol"] == "TEL"
        assert data["current_price"] == 1250.0

    _run(mcp_in_memory_session(_test))


def test_mcp_server_signals_unknown_symbol(mcp_in_memory_session):
    async def _test(session):
        result = await session.call_tool("validate_symbol", {"symbol": "ZZZZZ"})
        assert result.isError is True
        text = "\n".join(getattr(c, "text", "") for c in (result.content or []))
        assert "SymbolNotFoundError:" in text

    _run(mcp_in_memory_session(_test))


def test_mcp_server_validates_known_symbol(mcp_in_memory_session):
    async def _test(session):
        result = await session.call_tool("validate_symbol", {"symbol": "tel"})
        assert result.isError is False
        # FastMCP wraps scalar return types under {"result": value}.
        assert result.structuredContent in ({"result": "TEL"}, "TEL")

    _run(mcp_in_memory_session(_test))


# ---------------------------------------------------------------------------
# Façade dispatch behaviour
# ---------------------------------------------------------------------------


def _clear_settings_cache() -> None:
    from ph_stocks_advisor.infra import config

    config.get_settings.cache_clear()  # type: ignore[attr-defined]


def test_facade_uses_local_impl_via_mcp_stub():
    """The conftest MCP stub routes the façade call to the in-process service."""
    sample = StockPrice(symbol="TEL", current_price=999.0)
    with patch(
        "ph_stocks_advisor.data.services.price.fetch_stock_price",
        return_value=sample,
    ) as local_impl:
        result = tools.fetch_stock_price("TEL")

    assert result == sample
    local_impl.assert_called_once_with("TEL")


def test_facade_dispatches_through_mcp_client(monkeypatch):
    """The façade must call ``get_client().call(tool_name, args)``."""
    fake_payload = {"symbol": "TEL", "current_price": 1234.5}

    class _FakeClient:
        def call(self, tool_name, args):
            assert tool_name == "get_stock_price"
            assert args == {"symbol": "TEL"}
            return fake_payload

    with patch("ph_stocks_advisor.data.tools.get_client", return_value=_FakeClient()):
        result = tools.fetch_stock_price("TEL")

    assert isinstance(result, StockPrice)
    assert result.current_price == 1234.5


def test_facade_translates_mcp_error_to_symbol_not_found():
    class _FakeClient:
        def call(self, tool_name, args):
            raise SymbolNotFoundError("Symbol 'ZZZZZ' is not listed.")

    fake_client = _FakeClient()
    with (
        patch("ph_stocks_advisor.data.tools.get_client", return_value=fake_client),
        pytest.raises(SymbolNotFoundError),
    ):
        tools.validate_symbol("ZZZZZ")


@pytest.mark.no_mcp_stub
def test_get_client_raises_when_url_missing(monkeypatch):
    """An unset MCP_SERVER_URL must fail loudly with a clear message."""
    from ph_stocks_advisor.data.mcp_client import MCPNotConfiguredError, get_client
    from ph_stocks_advisor.infra.config import Settings

    monkeypatch.setattr(Settings, "mcp_server_url", "")
    reset_client()
    _clear_settings_cache()

    with pytest.raises(MCPNotConfiguredError, match="MCP_SERVER_URL is not set"):
        get_client()

    _clear_settings_cache()


@pytest.mark.no_mcp_stub
def test_facade_propagates_mcp_not_configured_error(monkeypatch):
    """The data façade must surface MCPNotConfiguredError to callers."""
    from ph_stocks_advisor.data.mcp_client import MCPNotConfiguredError
    from ph_stocks_advisor.infra.config import Settings

    monkeypatch.setattr(Settings, "mcp_server_url", "")
    reset_client()
    _clear_settings_cache()

    with pytest.raises(MCPNotConfiguredError):
        tools.fetch_stock_price("TEL")

    _clear_settings_cache()
