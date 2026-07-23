"""Symbol-validation robustness (prod incident 2026-07-23).

AREIT — a listed PSE REIT — was rejected as "not listed" during a DragonFi
outage, and the rejection crashed the Celery task instead of surfacing the
clean error page. Guards:

1. A transient upstream failure is never reported as a definitive
   "not listed" (SymbolValidationUnavailableError vs SymbolNotFoundError).
2. An empty stock-code list is never cached for the process lifetime.
3. The MCP client unwraps the not-found marker even when the framework
   wraps it ("Error executing tool <name>: ..." — mcp>=1.28 format).
4. The workflow validate node turns infra failures into a clean error
   state instead of an unhandled crash.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ph_stocks_advisor.data.clients import dragonfi
from ph_stocks_advisor.data.clients.dragonfi import (
    SymbolNotFoundError,
    SymbolValidationUnavailableError,
)


@pytest.fixture(autouse=True)
def _fresh_code_cache():
    dragonfi._STOCK_CODES_CACHE = None
    yield
    dragonfi._STOCK_CODES_CACHE = None


def _edge(result):
    """Patch the PSE EDGE fallback (imported inside validate_pse_symbol)."""
    return patch("ph_stocks_advisor.data.clients.pse_edge.symbol_exists", return_value=result)


# ---------------------------------------------------------------------------
# 1 + 2. DragonFi validation semantics
# ---------------------------------------------------------------------------


def test_total_outage_raises_unavailable_not_notfound():
    """DragonFi down AND PSE EDGE down → transient error, never a
    definitive "not listed"."""
    with (
        patch.object(dragonfi, "_get", return_value=None),
        _edge(None),
        pytest.raises(SymbolValidationUnavailableError),
    ):
        dragonfi.validate_pse_symbol("AREIT")


def test_dragonfi_down_but_edge_confirms_symbol():
    """The 2026-07-23 incident: DragonFi 515s, PSE EDGE up — a listed
    symbol must validate via the exchange's own registry."""
    with patch.object(dragonfi, "_get", return_value=None), _edge(True):
        assert dragonfi.validate_pse_symbol("AREIT") == "AREIT"


def test_dragonfi_down_but_edge_definitively_rejects():
    """EDGE searched successfully and found no match → real not-found even
    while DragonFi is down."""
    with patch.object(dragonfi, "_get", return_value=None), _edge(False), pytest.raises(SymbolNotFoundError):
        dragonfi.validate_pse_symbol("ZZZZZ")


def test_newly_listed_symbol_absent_from_dragonfi_universe():
    """Universe loaded but stale (symbol missing) + EDGE confirms → valid."""

    def fake_get(path, params=None):
        if "GetStockProfileList" in path:
            return [{"stockCode": "TEL"}]
        return None

    with patch.object(dragonfi, "_get", side_effect=fake_get), _edge(True):
        assert dragonfi.validate_pse_symbol("NEWIPO") == "NEWIPO"


def test_genuinely_unknown_symbol_still_rejected():
    """When the listing universe IS available, an absent symbol is a real
    not-found."""

    def fake_get(path, params=None):
        if "GetStockProfileList" in path:
            return [{"stockCode": "TEL"}, {"stockCode": "AREIT"}]
        return None  # profile probe: 204 for unknown

    with patch.object(dragonfi, "_get", side_effect=fake_get), _edge(None):
        assert dragonfi.validate_pse_symbol("AREIT") == "AREIT"
        with pytest.raises(SymbolNotFoundError):
            dragonfi.validate_pse_symbol("ZZZZZ")


def test_empty_code_list_is_not_cached():
    """A failed list fetch must be retried on the next call — an lru_cache
    once pinned the empty list for the whole process lifetime."""
    calls = {"n": 0}

    def flaky_get(path, params=None):
        if "GetStockProfileList" in path:
            calls["n"] += 1
            return None if calls["n"] == 1 else [{"stockCode": "AREIT"}]
        return None

    with patch.object(dragonfi, "_get", side_effect=flaky_get), _edge(None):
        with pytest.raises(SymbolValidationUnavailableError):
            dragonfi.validate_pse_symbol("AREIT")  # outage
        assert dragonfi.validate_pse_symbol("AREIT") == "AREIT"  # recovered
    assert calls["n"] == 2  # list was re-fetched, not served from cache


def test_nonempty_code_list_is_cached():
    calls = {"n": 0}

    def counting_get(path, params=None):
        if "GetStockProfileList" in path:
            calls["n"] += 1
            return [{"stockCode": "AREIT"}]
        return None

    with patch.object(dragonfi, "_get", side_effect=counting_get):
        dragonfi.validate_pse_symbol("AREIT")
        dragonfi.validate_pse_symbol("AREIT")
    assert calls["n"] == 1


# ---------------------------------------------------------------------------
# 3. MCP client unwraps the wrapped marker (mcp>=1.28 error format)
# ---------------------------------------------------------------------------


def test_client_unwraps_wrapped_not_found_marker():
    import asyncio

    from ph_stocks_advisor.data.mcp_client import _SyncMCPClient

    client = _SyncMCPClient.__new__(_SyncMCPClient)
    client._session = MagicMock()

    wrapped = MagicMock()
    wrapped.isError = True
    block = MagicMock()
    block.text = (
        "Error executing tool validate_symbol: SymbolNotFoundError: "
        "Symbol 'ZZZZZ' is not listed on the Philippine Stock Exchange."
    )
    wrapped.content = [block]

    async def fake_call_tool(name, args):
        return wrapped

    client._session.call_tool = fake_call_tool
    with pytest.raises(SymbolNotFoundError, match="ZZZZZ"):
        asyncio.run(client._call_async("validate_symbol", {"symbol": "ZZZZZ"}))


# ---------------------------------------------------------------------------
# 4. Workflow validate node: infra failure → clean error, not a crash
# ---------------------------------------------------------------------------


def test_validate_node_turns_infra_failure_into_clean_error():
    import ph_stocks_advisor.graph.workflow as wf
    from ph_stocks_advisor.data.mcp_client import MCPClientError

    node = wf._make_validate_node()
    with patch.object(wf, "validate_symbol", side_effect=MCPClientError("MCP tool 'validate_symbol' failed: boom")):
        out = node({"symbol": "AREIT"})
    err = out.get("error") or ""
    assert "temporarily unavailable" in err
    assert "not listed" not in err  # never a definitive rejection


def test_validate_node_still_rejects_unknown_symbols_cleanly():
    import ph_stocks_advisor.graph.workflow as wf

    node = wf._make_validate_node()
    with patch.object(wf, "validate_symbol", side_effect=SymbolNotFoundError("Symbol 'ZZZZZ' is not listed")):
        out = node({"symbol": "ZZZZZ"})
    assert "not listed" in (out.get("error") or "")


# ---------------------------------------------------------------------------
# 5. PSE EDGE price-snapshot fallback (DragonFi down)
# ---------------------------------------------------------------------------

_EDGE_STOCKDATA_HTML = """
<table>
<tr><th>Last Traded Price</th><td>37.05</td></tr>
<tr><th>Previous Close and Date</th><td>37.25<br/>(Jul 22, 2026)</td></tr>
<tr><th>52-Week High</th><td>45.50</td></tr>
<tr><th>52-Week Low</th><td>36.10</td></tr>
<tr><th>Outstanding Shares</th><td>4,156,887,818</td></tr>
<tr><th>P/E Ratio</th><td></td></tr>
</table>
"""


def test_edge_snapshot_parses_stockdata_page():
    from ph_stocks_advisor.data.clients import pse_edge

    resp = MagicMock(status_code=200, text=_EDGE_STOCKDATA_HTML)
    with (
        patch.object(pse_edge, "_resolve_cmpy_id", return_value="679"),
        patch.object(pse_edge.requests, "get", return_value=resp),
    ):
        snap = pse_edge.fetch_stock_snapshot("AREIT")
    assert snap == {
        "price": 37.05,
        "previous_close": 37.25,
        "week_high": 45.5,
        "week_low": 36.1,
        "shares_outstanding": 4_156_887_818.0,
    }


def test_edge_snapshot_none_when_company_unresolvable():
    from ph_stocks_advisor.data.clients import pse_edge

    with patch.object(pse_edge, "_resolve_cmpy_id", return_value=None):
        assert pse_edge.fetch_stock_snapshot("AREIT") is None


def test_price_service_falls_back_to_edge_when_dragonfi_down():
    from ph_stocks_advisor.data.services import price as price_mod

    snap = {
        "price": 37.05,
        "previous_close": 37.25,
        "week_high": 45.5,
        "week_low": 36.1,
        "shares_outstanding": 4e9,
    }
    with (
        patch.object(price_mod, "fetch_stock_profile", return_value={}),
        patch(
            "ph_stocks_advisor.data.clients.pse_edge.fetch_stock_snapshot",
            return_value=snap,
        ),
    ):
        sp = price_mod.fetch_stock_price("AREIT")
    assert sp.current_price == 37.05
    assert sp.previous_close == 37.25
    assert sp.fifty_two_week_high == 45.5


def test_price_service_minimal_when_both_sources_down():
    from ph_stocks_advisor.data.services import price as price_mod

    with (
        patch.object(price_mod, "fetch_stock_profile", return_value={}),
        patch(
            "ph_stocks_advisor.data.clients.pse_edge.fetch_stock_snapshot",
            return_value=None,
        ),
    ):
        sp = price_mod.fetch_stock_price("AREIT")
    assert sp.current_price == 0.0  # empty -> price agent degrades to a data gap
