"""
DragonFi API client for Philippine Stock Exchange (PSE) data.

Uses the public DragonFi Securities API (``https://api.dragonfi.ph/api/v2``)
to fetch real-time stock data for all PSE-listed securities.

This module serves two purposes:
1. **Symbol validation** — confirm a ticker is a real PSE stock.
2. **Primary data source** — provide price, dividend, valuation and
   financial metrics that may be missing from Yahoo Finance for some
   PSE tickers.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

import requests

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.dragonfi.ph/api/v2"
_TIMEOUT = 15  # seconds


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any] | list | None:
    """Perform a GET request against the DragonFi API.

    Returns the parsed JSON on success, or *None* when the server replies
    with a non-200 status (e.g. 204 for unknown symbols).
    """
    url = f"{_BASE_URL}/{path}"
    try:
        resp = requests.get(url, params=params, timeout=_TIMEOUT)
        if resp.status_code == 200:
            return resp.json()
        logger.debug("DragonFi %s returned status %s", url, resp.status_code)
        return None
    except requests.RequestException as exc:
        logger.warning("DragonFi request failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Symbol validation
# ---------------------------------------------------------------------------

class SymbolNotFoundError(Exception):
    """Raised when a ticker cannot be found on PSE via DragonFi."""


@lru_cache(maxsize=1)
def _fetch_all_stock_codes() -> frozenset[str]:
    """Return the set of all common-stock codes listed on DragonFi.

    The result is cached for the lifetime of the process so that repeated
    validations don't hit the network.
    """
    data = _get("Securities/GetStockProfileList", {"isPreferredStock": "false"})
    if data and isinstance(data, list):
        codes = frozenset(
            item["stockCode"].upper()
            for item in data
            if isinstance(item, dict) and "stockCode" in item
        )
        logger.info("Loaded %d PSE stock codes from DragonFi", len(codes))
        return codes
    return frozenset()


def validate_pse_symbol(symbol: str) -> str:
    """Validate that *symbol* is a real PSE stock via DragonFi.

    Returns the canonical (upper-case) stock code.

    Raises:
        SymbolNotFoundError: if the symbol is not found.
    """
    clean = symbol.upper().replace(".PS", "")
    all_codes = _fetch_all_stock_codes()

    if clean in all_codes:
        return clean

    # Fallback: directly query the profile endpoint (handles preferred
    # shares & newly listed tickers not yet in the cached list).
    profile = _get("Securities/GetStockProfile", {"stockCode": clean})
    if profile and isinstance(profile, dict) and profile.get("stockCode"):
        return profile["stockCode"].upper()

    raise SymbolNotFoundError(
        f"Symbol '{clean}' is not listed on the Philippine Stock Exchange. "
        f"Please verify the ticker at https://dragonfi.ph/market/stocks/"
    )


# ---------------------------------------------------------------------------
# Data-fetching functions
# ---------------------------------------------------------------------------

def fetch_stock_profile(symbol: str) -> dict[str, Any]:
    """Fetch the full stock profile from DragonFi.

    Returns a dict with keys such as ``price``, ``prevDayClosePrice``,
    ``weekHigh52``, ``weekLow52``, ``dividendYield``, ``sharesOutstanding``,
    ``companyName``, etc.  Returns an empty dict on failure.
    """
    data = _get("Securities/GetStockProfile", {"stockCode": symbol.upper()})
    return data if isinstance(data, dict) else {}


def fetch_security_valuation(symbol: str) -> dict[str, Any]:
    """Fetch annual valuation multiples (PE, PB, EV/EBITDA, …).

    Returns the raw API response dict, or empty dict on failure.
    """
    data = _get("Securities/GetSecurityValuation", {"stockCode": symbol.upper()})
    return data if isinstance(data, dict) else {}


def fetch_security_metrics(symbol: str) -> dict[str, Any]:
    """Fetch financial metrics (ROE, FCF, debt ratios, …).

    Returns the raw API response dict, or empty dict on failure.
    """
    data = _get("Securities/GetSecurityMetrics", {"stockCode": symbol.upper()})
    return data if isinstance(data, dict) else {}


def fetch_stock_financials(symbol: str) -> dict[str, Any]:
    """Fetch income / balance-sheet / cash-flow statements.

    Returns the raw API response dict, or empty dict on failure.
    """
    data = _get("Securities/GetStockFinancialStatements", {"stockCode": symbol.upper()})
    return data if isinstance(data, dict) else {}


def fetch_stock_news(symbol: str, page_size: int = 5) -> list[dict[str, Any]]:
    """Fetch recent news articles for *symbol* (newest first).

    Returns up to *page_size* articles as dicts with keys like
    ``title``, ``description``, ``publishDate``, ``source``, etc.
    """
    data = _get(
        "News/GetNews",
        {
            "PageNum": 1,
            "PageSize": page_size,
            "isShowPortfolioNews": "false",
            "StockCode": symbol.upper(),
            "SortBy": "PublishDate",
            "Asc": "false",
        },
    )
    if data and isinstance(data, dict):
        return data.get("news", [])
    if isinstance(data, list):
        return data[:page_size]
    return []


# ---------------------------------------------------------------------------
# Financial trend helpers
# ---------------------------------------------------------------------------

def _extract_annual_values(series_data: dict[str, Any] | None) -> dict[str, float]:
    """Extract {year: value} from a DragonFi annual series dict.

    DragonFi annual metrics use keys like ``"2020"``, ``"2021"``, etc.
    for the raw values and ``"2020_YoY"`` for YoY change strings.
    We only return the raw-value keys.
    """
    if not series_data or not isinstance(series_data, dict):
        return {}
    result: dict[str, float] = {}
    for key, val in series_data.items():
        # Skip non-year keys (Symbol, Item) and YoY keys
        if "_" in key or not key.isdigit() or val is None:
            continue
        try:
            result[key] = float(val)
        except (TypeError, ValueError):
            continue
    return result


def fetch_annual_income_trends(symbol: str) -> dict[str, dict[str, float]]:
    """Return multi-year revenue, net income, and operating income trends.

    Returns a dict with keys ``"revenue"``, ``"net_income"``,
    ``"operation_income"`` each mapping to ``{year: value}``.
    """
    stmts = fetch_stock_financials(symbol)
    if not stmts:
        return {}

    annual_is = stmts.get("incomeStatementAnnual", {})
    return {
        "revenue": _extract_annual_values(annual_is.get("revenue")),
        "net_income": _extract_annual_values(annual_is.get("netIncome")),
        "operation_income": _extract_annual_values(annual_is.get("operationIncome")),
    }


def fetch_annual_cashflow_trends(symbol: str) -> dict[str, dict[str, float]]:
    """Return multi-year cash-flow trends (CFO, CFI, CFF, FCF).

    Returns a dict with keys ``"cfo"``, ``"cfi"``, ``"cff"`` from cash-flow
    statements and ``"fcf"`` from security metrics.
    """
    stmts = fetch_stock_financials(symbol)
    cf_annual = stmts.get("cashFlowAnnual", {}) if stmts else {}

    metrics = fetch_security_metrics(symbol)
    fcf_annual = {}
    if metrics:
        cf_metrics = metrics.get("cashFlowAnnual", {})
        fcf_data = cf_metrics.get("fcf", {})
        fcf_annual = _extract_annual_values(fcf_data)

    return {
        "cfo": _extract_annual_values(cf_annual.get("cfo")),
        "cfi": _extract_annual_values(cf_annual.get("cfi")),
        "cff": _extract_annual_values(cf_annual.get("cff")),
        "fcf": fcf_annual,
    }
