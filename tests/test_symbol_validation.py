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
    from ph_stocks_advisor.data.clients import pse_edge

    dragonfi._STOCK_CODES_CACHE = None
    pse_edge._CMPY_ID_CACHE.clear()
    pse_edge._IS_REIT_CACHE.clear()
    yield
    dragonfi._STOCK_CODES_CACHE = None
    pse_edge._CMPY_ID_CACHE.clear()
    pse_edge._IS_REIT_CACHE.clear()


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


def test_dragonfi_down_edge_no_match_is_transient_not_rejected():
    """Outage + EDGE no-match must fail TRANSIENTLY: EDGE's autocomplete
    omits preferred shares (GTPPB/SMC2I return [] despite being listed), so
    its miss alone can never justify a definitive "not listed"."""
    with (
        patch.object(dragonfi, "_get", return_value=None),
        _edge(False),
        pytest.raises(SymbolValidationUnavailableError),
    ):
        dragonfi.validate_pse_symbol("GTPPB")


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


# ---------------------------------------------------------------------------
# 6. PSE EDGE annual-financials fallback (valuation + dividend enrichment)
# ---------------------------------------------------------------------------

_EDGE_FIN_HTML = """
<h3>Annual</h3>
<table><tr><th>Item</th><th>Current Year</th><th>Previous Year</th></tr>
<tr><th>Current Assets</th><td>7,288,692,975</td><td>4,557,171,311</td></tr>
<tr><th>Book Value Per Share</th><td>36.57</td><td>35.32</td></tr></table>
<table><tr><th>Item</th><th>Current Year</th><th>Previous Year</th></tr>
<tr><th>Gross Revenue</th><td>12,959,780,593</td><td>10,259,166,947</td></tr>
<tr><th>Net Income/(Loss) After Tax</th><td>9,539,219,827</td><td>7,317,064,621</td></tr>
<tr><th>Earnings/(Loss) Per Share (Basic)</th><td>2.75</td><td>2.62</td></tr></table>
<h3>Quarterly</h3>
<table><tr><th>Item</th><th>Period Ended</th><th>Fiscal Year Ended(Audited)</th></tr>
<tr><th>Gross Revenue</th><td>3,544,796,053</td><td>2,920,729,793</td></tr></table>
<p>Dec 31, 2025</p><p>Mar 31, 2026</p>
"""

_FIN = {
    "fiscal_year": 2025,
    "revenue": (12_959_780_593.0, 10_259_166_947.0),
    "net_income": (9_539_219_827.0, 7_317_064_621.0),
    "eps": 2.75,
    "eps_previous": 2.62,
    "book_value_per_share": 36.57,
    "bvps_previous": 35.32,
}


def test_edge_annual_financials_parser():
    from ph_stocks_advisor.data.clients import pse_edge

    resp = MagicMock(status_code=200, text=_EDGE_FIN_HTML)
    with (
        patch.object(pse_edge, "_resolve_cmpy_id", return_value="679"),
        patch.object(pse_edge.requests, "get", return_value=resp),
    ):
        fin = pse_edge.fetch_annual_financials("AREIT")
    assert fin is not None
    assert fin["fiscal_year"] == 2025  # earliest date = audited FY end
    assert fin["eps"] == 2.75
    assert fin["book_value_per_share"] == 36.57
    assert fin["net_income"] == (9_539_219_827.0, 7_317_064_621.0)
    revenue = fin["revenue"]
    assert isinstance(revenue, tuple)
    assert revenue[0] == 12_959_780_593.0  # annual, not the quarterly 3.5B


def test_valuation_falls_back_to_edge_graham():
    from ph_stocks_advisor.data.services import valuation as val_mod

    snap = {"price": 37.05, "previous_close": 37.25, "week_high": 45.5, "week_low": 36.1, "shares_outstanding": 4e9}
    with (
        patch.object(val_mod, "fetch_stock_profile", return_value={}),
        patch.object(val_mod, "fetch_security_valuation", return_value={}),
        patch("ph_stocks_advisor.data.clients.pse_edge.fetch_stock_snapshot", return_value=snap),
        patch("ph_stocks_advisor.data.clients.pse_edge.fetch_annual_financials", return_value=dict(_FIN)),
        patch("ph_stocks_advisor.data.clients.pse_edge.is_reit_from_edge", return_value=False),
    ):
        fv = val_mod.fetch_fair_value("AREIT")
    assert fv.current_price == 37.05
    assert fv.pe_ratio == round(37.05 / 2.75, 2)
    assert fv.pb_ratio == round(37.05 / 36.57, 2)
    assert fv.estimated_fair_value > 0  # Graham number from EPS + BVPS
    assert fv.is_reit is False  # EDGE registry answered non-REIT


def test_dividend_outage_enrichment_from_edge():
    from ph_stocks_advisor.data.services import dividend as div_mod

    with (
        patch.object(div_mod, "fetch_stock_profile", return_value={}),  # OUTAGE
        patch.object(div_mod, "fetch_company_dividend_announcements", return_value=[]),
        patch("ph_stocks_advisor.data.clients.pse_edge.fetch_annual_financials", return_value=dict(_FIN)),
    ):
        info = div_mod.fetch_dividend_info("AREIT")
    assert info.net_income_trend == {"2024": 7_317_064_621.0, "2025": 9_539_219_827.0}
    assert info.revenue_trend["2025"] == 12_959_780_593.0
    assert "unavailable" in info.dividend_sustainability_note.lower()


def test_no_dividend_stock_with_live_profile_stays_a_clean_gap():
    """A live DragonFi profile with zero yield must NOT be EDGE-enriched —
    a pays-no-dividend stock keeps its clean "no dividend history" gap."""
    from ph_stocks_advisor.agents.specialists import _is_empty_dividend_info
    from ph_stocks_advisor.data.services import dividend as div_mod

    with patch.object(div_mod, "fetch_stock_profile", return_value={"dividendYield": 0, "price": 1.0}):
        info = div_mod.fetch_dividend_info("DITO")
    assert _is_empty_dividend_info(info)  # -> dividend agent degrades to a gap


# ---------------------------------------------------------------------------
# 7. Review hardening: negatives, fiscal-year window, cmpy_id memo
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("12,959,780,593", 12_959_780_593.0),
        ("(1,234.56)", -1234.56),  # PSE filing convention
        ("-1,234.56", -1234.56),  # minus-sign convention
        ("", 0.0),
    ],
)
def test_num_parses_both_negative_conventions(raw, expected):
    from ph_stocks_advisor.data.clients.pse_edge import _num

    assert _num(raw) == expected


def test_fiscal_year_ignores_stray_old_dates():
    """A footer/disclosure date years in the past must not become the
    fiscal-year label for the trend dict."""
    from datetime import datetime

    from ph_stocks_advisor.data.clients import pse_edge

    this_year = datetime.now().year
    html = _EDGE_FIN_HTML.replace(
        "<p>Dec 31, 2025</p>",
        f"<p>Copyright Jan 01, 2010</p><p>Dec 31, {this_year - 1}</p>",
    ).replace("<p>Mar 31, 2026</p>", f"<p>Mar 31, {this_year}</p>")
    resp = MagicMock(status_code=200, text=html)
    with (
        patch.object(pse_edge, "_resolve_cmpy_id", return_value="679"),
        patch.object(pse_edge.requests, "get", return_value=resp),
    ):
        fin = pse_edge.fetch_annual_financials("AREIT")
    assert fin is not None
    assert fin["fiscal_year"] == this_year - 1  # not 2010


def test_cmpy_id_lookup_is_memoised():
    from ph_stocks_advisor.data.clients import pse_edge

    resp = MagicMock(status_code=200)
    resp.json.return_value = [{"cmpyId": "679", "cmpyNm": "AREIT, Inc.", "symbol": "AREIT"}]
    with patch.object(pse_edge.requests, "get", return_value=resp) as mock_get:
        assert pse_edge._resolve_cmpy_id("AREIT") == "679"
        assert pse_edge._resolve_cmpy_id("AREIT") == "679"
    assert mock_get.call_count == 1  # second call served from the memo


# ---------------------------------------------------------------------------
# 8. REIT status from the EDGE company registry (outage fallback)
# ---------------------------------------------------------------------------


def _edge_reit(result):
    return patch("ph_stocks_advisor.data.clients.pse_edge.is_reit_from_edge", return_value=result)


def test_is_reit_from_edge_name_signal():
    """A company name containing REIT is definitive — no page fetch needed."""
    from ph_stocks_advisor.data.clients import pse_edge

    pse_edge._IS_REIT_CACHE.clear()
    ac = MagicMock(status_code=200)
    ac.json.return_value = [{"cmpyId": "691", "cmpyNm": "Citicore Energy REIT Corp.", "symbol": "CREIT"}]
    with patch.object(pse_edge.requests, "get", return_value=ac) as mock_get:
        assert pse_edge.is_reit_from_edge("CREIT") is True
    assert mock_get.call_count == 1  # autocomplete only


def test_is_reit_from_edge_description_signal_and_sponsor_negative():
    from ph_stocks_advisor.data.clients import pse_edge

    pse_edge._IS_REIT_CACHE.clear()

    def fake_get(url, params=None, headers=None, timeout=None):
        params = params or {}
        if "autoComplete" in url:
            resp = MagicMock(status_code=200)
            resp.json.return_value = [
                {"cmpyId": "679", "cmpyNm": "AREIT, Inc.", "symbol": "AREIT"},
                {"cmpyId": "180", "cmpyNm": "Ayala Land, Inc.", "symbol": "ALI"},
            ]
            return resp
        page = MagicMock(status_code=200)
        if params["cmpy_id"] == "679":
            page.text = "<b>Company Description</b> AREIT is a commercial Real Estate Investment Trust <b>Sector</b>"
        else:
            page.text = "<b>Company Description</b> Ayala Land is a property developer <b>Sector</b>"
        return page

    with patch.object(pse_edge.requests, "get", side_effect=fake_get):
        assert pse_edge.is_reit_from_edge("AREIT") is True
        assert pse_edge.is_reit_from_edge("ALI") is False
    pse_edge._IS_REIT_CACHE.clear()


def test_is_reit_from_edge_unreachable_is_unknown():
    from ph_stocks_advisor.data.clients import pse_edge

    pse_edge._IS_REIT_CACHE.clear()
    with patch.object(pse_edge.requests, "get", side_effect=OSError("down")):
        assert pse_edge.is_reit_from_edge("AREIT") is None


def test_valuation_fallback_gets_reit_status_from_edge():
    from ph_stocks_advisor.data.services import valuation as val_mod

    snap = {"price": 37.05, "previous_close": 37.25, "week_high": 45.5, "week_low": 36.1, "shares_outstanding": 4e9}
    with (
        patch.object(val_mod, "fetch_stock_profile", return_value={}),
        patch.object(val_mod, "fetch_security_valuation", return_value={}),
        patch("ph_stocks_advisor.data.clients.pse_edge.fetch_stock_snapshot", return_value=snap),
        patch("ph_stocks_advisor.data.clients.pse_edge.fetch_annual_financials", return_value=dict(_FIN)),
        _edge_reit(True),
    ):
        assert val_mod.fetch_fair_value("AREIT").is_reit is True


def test_sentiment_outage_gets_reit_status_from_edge():
    from ph_stocks_advisor.data.services import sentiment as sent_mod

    with (
        patch("ph_stocks_advisor.data.clients.dragonfi.fetch_stock_profile", return_value={}),
        patch("ph_stocks_advisor.data.clients.tavily_search.search_global_events", return_value=""),
        patch("ph_stocks_advisor.data.clients.tavily_search.search_bsp_rate", return_value=""),
        _edge_reit(True),
    ):
        info = sent_mod.fetch_sentiment_info("AREIT")
    assert info.is_reit is True


def test_dividend_outage_gets_reit_status_from_edge():
    from ph_stocks_advisor.data.services import dividend as div_mod

    with (
        patch.object(div_mod, "fetch_stock_profile", return_value={}),
        patch.object(div_mod, "fetch_company_dividend_announcements", return_value=[]),
        patch("ph_stocks_advisor.data.clients.pse_edge.fetch_annual_financials", return_value=dict(_FIN)),
        _edge_reit(True),
    ):
        info = div_mod.fetch_dividend_info("AREIT")
    assert info.is_reit is True


def test_unknown_reit_status_maps_to_false_never_true():
    from ph_stocks_advisor.data.services import valuation as val_mod

    snap = {"price": 37.05, "previous_close": 37.25, "week_high": 45.5, "week_low": 36.1, "shares_outstanding": 4e9}
    with (
        patch.object(val_mod, "fetch_stock_profile", return_value={}),
        patch.object(val_mod, "fetch_security_valuation", return_value={}),
        patch("ph_stocks_advisor.data.clients.pse_edge.fetch_stock_snapshot", return_value=snap),
        patch("ph_stocks_advisor.data.clients.pse_edge.fetch_annual_financials", return_value=dict(_FIN)),
        _edge_reit(None),  # EDGE unreachable -> unknown
    ):
        assert val_mod.fetch_fair_value("AREIT").is_reit is False
