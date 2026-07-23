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


# symbol → cmpy_id memo (stable mappings; only successes are cached).
_CMPY_ID_CACHE: dict[str, str] = {}


def _resolve_cmpy_id(symbol: str) -> str | None:
    """Look up the PSE EDGE ``cmpy_id`` for a ticker symbol.

    Uses the autocomplete endpoint and returns the first exact match.
    Successful lookups are memoised — one analysis touches this from the
    price, valuation, and dividend fallbacks, and the mapping never changes.
    """
    cached = _CMPY_ID_CACHE.get(symbol.upper())
    if cached:
        return cached
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
                cmpy_id = str(item["cmpyId"])
                _CMPY_ID_CACHE[symbol.upper()] = cmpy_id
                return cmpy_id

        logger.debug("PSE EDGE autocomplete: no exact match for %s in %s", symbol, results)
        return None

    except Exception as exc:
        logger.warning("PSE EDGE autocomplete failed for %s: %s", symbol, exc)
        return None


def symbol_exists(symbol: str) -> bool | None:
    """Tri-state PSE EDGE listing check via the autocomplete endpoint.

    Returns ``True`` when an exact symbol match exists, ``False`` when the
    endpoint answered but had no match (definitively not listed), and
    ``None`` when the endpoint could not be reached — callers must treat
    ``None`` as "unknown", never as "not listed". Used as the validation
    fallback when DragonFi is down (e.g. its 2026-07-23 HTTP 515 outage).
    """
    clean = symbol.upper().replace(".PS", "")
    try:
        resp = requests.get(
            f"{_base_url()}/autoComplete/searchCompanyNameSymbol.ax",
            params={"term": clean},
            headers={"X-Requested-With": "XMLHttpRequest"},
            timeout=_timeout(),
        )
        if resp.status_code != 200:
            logger.warning("PSE EDGE symbol_exists: HTTP %s for %s", resp.status_code, clean)
            return None
        results = resp.json()
        if not isinstance(results, list):
            return None
        return any(isinstance(item, dict) and item.get("symbol", "").upper() == clean for item in results)
    except Exception as exc:
        logger.warning("PSE EDGE symbol_exists failed for %s: %s", clean, exc)
        return None


_SNAPSHOT_FIELDS = {
    "Last Traded Price": "price",
    "Previous Close and Date": "previous_close",
    "52-Week High": "week_high",
    "52-Week Low": "week_low",
    "Outstanding Shares": "shares_outstanding",
}


def fetch_stock_snapshot(symbol: str) -> dict[str, float] | None:
    """Scrape a market-data snapshot from the PSE EDGE stockData page.

    Fallback price source for when DragonFi is down (its 2026-07-23 HTTP 515
    outage left the price dimension with no data at all). Returns a dict with
    ``price``, ``previous_close``, ``week_high``, ``week_low`` and
    ``shares_outstanding`` (0.0 when a field is blank), or ``None`` when the
    page or the company lookup is unreachable. Valuation fields (P/E, Book
    Value) are deliberately NOT scraped — EDGE frequently leaves them blank,
    so they cannot be a reliable fallback.
    """
    clean = symbol.upper().replace(".PS", "")
    cmpy_id = _resolve_cmpy_id(clean)
    if not cmpy_id:
        return None
    try:
        resp = requests.get(
            f"{_base_url()}/companyPage/stockData.do",
            params={"cmpy_id": cmpy_id},
            headers={"Referer": _base_url()},
            timeout=_timeout(),
        )
        if resp.status_code != 200:
            logger.warning("PSE EDGE stockData returned %s for %s", resp.status_code, clean)
            return None
        snapshot: dict[str, float] = {}
        for label, key in _SNAPSHOT_FIELDS.items():
            m = re.search(
                rf"<th[^>]*>\s*{re.escape(label)}\s*</th>\s*<td[^>]*>(.*?)</td>",
                resp.text,
                re.S,
            )
            raw = re.sub(r"<[^>]+>", "", m.group(1)).strip() if m else ""
            num = re.search(r"[\d,]+(?:\.\d+)?", raw)
            snapshot[key] = float(num.group(0).replace(",", "")) if num else 0.0
        if snapshot.get("price", 0.0) <= 0:
            logger.warning("PSE EDGE stockData had no traded price for %s", clean)
            return None
        logger.info("PSE EDGE snapshot for %s: price=%s", clean, snapshot["price"])
        return snapshot
    except Exception as exc:
        logger.warning("PSE EDGE stockData fetch failed for %s: %s", clean, exc)
        return None


def _num(raw: str) -> float:
    """Parse an EDGE financial figure.

    Handles both negative conventions: parenthesised "(123)" (the PSE
    filing style) and a leading minus "-123".
    """
    raw = raw.strip()
    neg = raw.startswith("(") and raw.endswith(")")
    m = re.search(r"-?[\d,]+(?:\.\d+)?", raw)
    if not m:
        return 0.0
    val = float(m.group(0).replace(",", ""))
    return -abs(val) if neg else val


def _parse_two_col_table(table_html: str) -> dict[str, tuple[float, float]]:
    """Parse an EDGE annual table: row-label THs each paired with two TDs
    (Current Year, Previous Year). The first three THs are column headers."""
    ths = [re.sub(r"<[^>]+>", "", x).strip() for x in re.findall(r"<th[^>]*>(.*?)</th>", table_html, re.S)]
    tds = [re.sub(r"<[^>]+>", "", x).strip() for x in re.findall(r"<td[^>]*>(.*?)</td>", table_html, re.S)]
    labels = ths[3:]  # skip Item / Current Year / Previous Year
    out: dict[str, tuple[float, float]] = {}
    for i, label in enumerate(labels):
        cur, prev = tds[2 * i : 2 * i + 2] + [""] * (2 - len(tds[2 * i : 2 * i + 2]))
        out[label] = (_num(cur), _num(prev))
    return out


def fetch_annual_financials(symbol: str) -> dict[str, object] | None:
    """Scrape audited annual fundamentals from the EDGE financial-reports page.

    Fallback source for when DragonFi's financial endpoints are down: the
    page renders the latest 17-A figures as structured HTML — revenue, net
    income, EPS and book value per share for the current and previous
    fiscal year. Returns ``None`` when the page or company lookup fails.
    Quarterly tables are ignored (annual audited figures only).
    """
    clean = symbol.upper().replace(".PS", "")
    cmpy_id = _resolve_cmpy_id(clean)
    if not cmpy_id:
        return None
    try:
        resp = requests.get(
            f"{_base_url()}/companyPage/financial_reports_view.do",
            params={"cmpy_id": cmpy_id},
            headers={"Referer": _base_url()},
            timeout=_timeout(),
        )
        if resp.status_code != 200:
            logger.warning("PSE EDGE financial reports returned %s for %s", resp.status_code, clean)
            return None
        tables = re.findall(r"<table[^>]*>(.*?)</table>", resp.text, re.S)
        balance = income = None
        for tb in tables:
            if ">Current Year<" not in tb:
                continue  # quarterly tables use different column headers
            if "Book Value Per Share" in tb and balance is None:
                balance = _parse_two_col_table(tb)
            elif "Gross Revenue" in tb and income is None:
                income = _parse_two_col_table(tb)
        if not income:
            return None
        # The audited fiscal-year end is the earliest full date on the page
        # (interim period-ends are always later than or equal to it). Only
        # consider recent years so a stray old date elsewhere in the HTML
        # (footer, disclosure reference) cannot mislabel the trend.
        this_year = datetime.now().year
        years = [
            int(y)
            for y in re.findall(r"[A-Z][a-z]{2,8}\.? \d{1,2},? (\d{4})", resp.text)
            if this_year - 3 <= int(y) <= this_year + 1
        ]
        fiscal_year = min(years) if years else None
        bvps = (balance or {}).get("Book Value Per Share", (0.0, 0.0))
        eps = income.get("Earnings/(Loss) Per Share (Basic)", (0.0, 0.0))
        revenue = income.get("Gross Revenue", (0.0, 0.0))
        net_income = income.get("Net Income/(Loss) After Tax", (0.0, 0.0))
        result = {
            "fiscal_year": fiscal_year,
            "revenue": revenue,
            "net_income": net_income,
            "eps": eps[0],
            "eps_previous": eps[1],
            "book_value_per_share": bvps[0],
            "bvps_previous": bvps[1],
        }
        logger.info(
            "PSE EDGE annual financials for %s: FY%s eps=%s bvps=%s",
            clean,
            fiscal_year,
            eps[0],
            bvps[0],
        )
        return result
    except Exception as exc:
        logger.warning("PSE EDGE financial reports fetch failed for %s: %s", clean, exc)
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
                "PSE EDGE chart data returned %s for %s",
                resp.status_code,
                symbol,
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

            rows.append(
                {
                    "Date": dt_val,
                    "Open": float(rec.get("OPEN", 0)),
                    "High": float(rec.get("HIGH", 0)),
                    "Low": float(rec.get("LOW", 0)),
                    "Close": float(rec.get("CLOSE", 0)),
                    "Volume": float(rec.get("VALUE", 0)),
                }
            )

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        df.set_index("Date", inplace=True)
        df.sort_index(inplace=True)
        return df

    except Exception as exc:
        logger.warning("PSE EDGE chart fetch failed for %s: %s", symbol, exc)
        return pd.DataFrame()
