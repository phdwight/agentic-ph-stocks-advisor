"""
PSE EDGE historical OHLCV client for Philippine Stock Exchange equities.

Fetches daily Open/High/Low/Close/Value (volume in PHP) data from the PSE
EDGE charting endpoint — the same data that powers the interactive chart at
``https://edge.pse.com.ph/companyPage/stockData.do?cmpy_id=…``.

**Two-step resolution:**

1. Resolve the PSE EDGE ``cmpy_id`` for a ticker via the autocomplete API.
2. Scrape the stockData page to extract the ``security_id`` for common shares.
3. POST to ``/common/DisclosureCht.ax`` to get daily OHLCV.

No API key required — the PSE EDGE endpoints are public.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
import requests

logger = logging.getLogger(__name__)


def _base_url() -> str:
    from ph_stocks_advisor.infra.config import get_settings
    return get_settings().pse_edge_base_url


def _timeout() -> int:
    from ph_stocks_advisor.infra.config import get_settings
    return get_settings().http_timeout

# In-process cache: symbol → (cmpy_id, security_id)
_ID_CACHE: dict[str, tuple[str, str]] = {}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_cmpy_id(symbol: str) -> str | None:
    """Look up the PSE EDGE ``cmpy_id`` for a ticker symbol.

    Uses the autocomplete endpoint and returns the first exact match.
    """
    try:
        resp = requests.get(
            f"{_base_url()}/autoComplete/searchCompanyNameSymbol.ax",
            params={"term": symbol},
            headers={"X-Requested-With": "XMLHttpRequest"},
            timeout=_timeout(),
        )
        if resp.status_code != 200:
            logger.debug("PSE EDGE autocomplete returned %s for %s", resp.status_code, symbol)
            return None

        results: list[dict[str, Any]] = resp.json()
        for item in results:
            if item.get("symbol", "").upper() == symbol.upper():
                return str(item["cmpyId"])

        logger.debug("PSE EDGE autocomplete: no exact match for %s in %s", symbol, results)
        return None

    except Exception as exc:
        logger.warning("PSE EDGE autocomplete failed for %s: %s", symbol, exc)
        return None


def _resolve_security_id(cmpy_id: str) -> str | None:
    """Scrape the stockData page to get the ``security_id`` of common shares.

    The security_id is embedded in a ``<select name="security_id">`` dropdown.
    The *first* ``<option>`` (``selected``) is common shares.
    """
    try:
        resp = requests.get(
            f"{_base_url()}/companyPage/stockData.do",
            params={"cmpy_id": cmpy_id},
            timeout=_timeout(),
        )
        if resp.status_code != 200:
            logger.debug("PSE EDGE stockData page returned %s for cmpy_id=%s", resp.status_code, cmpy_id)
            return None

        # Extract the first <option value="NNN"...> under the security_id select
        match = re.search(
            r'<select\s+name="security_id"[^>]*>.*?<option\s+value="(\d+)"',
            resp.text,
            re.DOTALL,
        )
        if match:
            return match.group(1)

        logger.debug("PSE EDGE stockData page: security_id select not found for cmpy_id=%s", cmpy_id)
        return None

    except Exception as exc:
        logger.warning("PSE EDGE security_id scrape failed for cmpy_id=%s: %s", cmpy_id, exc)
        return None


def _resolve_ids(symbol: str) -> tuple[str, str] | None:
    """Resolve both ``cmpy_id`` and ``security_id`` for *symbol*, with caching."""
    symbol = symbol.upper()
    if symbol in _ID_CACHE:
        return _ID_CACHE[symbol]

    cmpy_id = _resolve_cmpy_id(symbol)
    if not cmpy_id:
        return None

    security_id = _resolve_security_id(cmpy_id)
    if not security_id:
        return None

    _ID_CACHE[symbol] = (cmpy_id, security_id)
    return cmpy_id, security_id


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_pse_edge_ohlcv(
    symbol: str,
    *,
    days: int = 365,
) -> pd.DataFrame:
    """Fetch daily OHLCV data from PSE EDGE for the last *days* trading days.

    Returns a DataFrame with a ``DatetimeIndex`` and columns:
    ``Open``, ``High``, ``Low``, ``Close``, ``Volume``
    (Volume is reported in PHP value; this mirrors the PSE EDGE chart).

    Returns an empty DataFrame on failure.
    """
    symbol = symbol.upper().replace(".PS", "")
    ids = _resolve_ids(symbol)
    if not ids:
        logger.info("Could not resolve PSE EDGE IDs for %s", symbol)
        return pd.DataFrame()

    cmpy_id, security_id = ids
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)

    try:
        base = _base_url()
        resp = requests.post(
            f"{base}/common/DisclosureCht.ax",
            json={
                "cmpy_id": cmpy_id,
                "security_id": security_id,
                "startDate": start_date.strftime("%m-%d-%Y"),
                "endDate": end_date.strftime("%m-%d-%Y"),
            },
            headers={
                "Content-Type": "application/json",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"{base}/companyPage/stockData.do?cmpy_id={cmpy_id}",
            },
            timeout=_timeout(),
        )
        if resp.status_code != 200:
            logger.warning(
                "PSE EDGE chart data returned %s for %s", resp.status_code, symbol,
            )
            return pd.DataFrame()

        data = resp.json()
        chart_data: list[dict[str, Any]] = data.get("chartData", [])
        if not chart_data:
            logger.info("PSE EDGE returned empty chartData for %s", symbol)
            return pd.DataFrame()

        # Build the DataFrame
        rows: list[dict[str, Any]] = []
        seen_dates: set[str] = set()
        for rec in chart_data:
            # PSE EDGE sometimes duplicates rows — deduplicate by date
            date_str = rec.get("CHART_DATE", "")
            if date_str in seen_dates:
                continue
            seen_dates.add(date_str)

            try:
                dt_val = datetime.strptime(date_str, "%b %d, %Y %H:%M:%S")
            except ValueError:
                continue

            rows.append({
                "Date": dt_val,
                "Open": float(rec.get("OPEN", 0)),
                "High": float(rec.get("HIGH", 0)),
                "Low": float(rec.get("LOW", 0)),
                "Close": float(rec.get("CLOSE", 0)),
                "Volume": float(rec.get("VALUE", 0)),
            })

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        df.set_index("Date", inplace=True)
        df.sort_index(inplace=True)
        return df

    except Exception as exc:
        logger.warning("PSE EDGE chart fetch failed for %s: %s", symbol, exc)
        return pd.DataFrame()
