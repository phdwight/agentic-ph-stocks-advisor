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
