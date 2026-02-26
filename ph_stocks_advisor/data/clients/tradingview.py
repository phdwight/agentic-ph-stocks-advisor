"""
TradingView scanner client for Philippine Stock Exchange (PSE) equities.

Uses TradingView's public scanner API to fetch real-time snapshot data that
is unavailable from DragonFi — specifically **performance** (weekly, monthly,
quarterly, annual) and **volatility** metrics.

These figures let the movement agent detect mid-year crashes or rallies that
would be hidden by a simple 52-week high/low comparison.

No API key required — the scanner endpoint is public.
"""

from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)


def _scanner_url() -> str:
    from ph_stocks_advisor.infra.config import get_settings
    return get_settings().tradingview_scanner_url


def _timeout() -> int:
    from ph_stocks_advisor.infra.config import get_settings
    return get_settings().http_timeout

# Columns we request from TradingView's scanner
_COLUMNS = [
    # Today's OHLCV
    "close", "open", "high", "low", "volume",
    # Performance (% change over period)
    "Perf.W", "Perf.1M", "Perf.3M", "Perf.6M", "Perf.Y", "Perf.YTD",
    # Volatility (annualised std-dev estimates)
    "Volatility.D", "Volatility.W", "Volatility.M",
    # 52-week extremes
    "price_52_week_high", "price_52_week_low",
]

_COLUMN_KEYS = [
    "close", "open", "high", "low", "volume",
    "perf_week", "perf_1m", "perf_3m", "perf_6m", "perf_year", "perf_ytd",
    "volatility_daily", "volatility_weekly", "volatility_monthly",
    "week_high_52", "week_low_52",
]


def fetch_tradingview_snapshot(symbol: str) -> dict[str, Any]:
    """Fetch a TradingView scanner snapshot for a PSE stock.

    Returns a dict with keys from ``_COLUMN_KEYS``, or an empty dict on
    failure.  Numeric values that TradingView reports as ``None`` are
    replaced with ``0.0``.
    """
    symbol = symbol.upper().replace(".PS", "")
    tv_symbol = f"PSE:{symbol}"

    try:
        resp = requests.post(
            _scanner_url(),
            json={
                "symbols": {"tickers": [tv_symbol]},
                "columns": _COLUMNS,
            },
            timeout=_timeout(),
        )
        if resp.status_code != 200:
            logger.debug(
                "TradingView scanner returned %s for %s", resp.status_code, tv_symbol,
            )
            return {}

        data = resp.json()
        rows = data.get("data", [])
        if not rows:
            return {}

        values = rows[0].get("d", [])
        result: dict[str, Any] = {}
        for key, val in zip(_COLUMN_KEYS, values):
            result[key] = float(val) if val is not None else 0.0
        return result

    except Exception as exc:
        logger.warning("TradingView scanner failed for %s: %s", tv_symbol, exc)
        return {}


def format_tv_performance_summary(snapshot: dict[str, Any]) -> str:
    """Format TradingView performance data into an LLM-friendly summary.

    Returns a string like:
        ``"1-week: +13.7%, 1-month: -9.5%, 3-month: -5.7%, …"``

    Returns an empty string if the snapshot is empty.
    """
    if not snapshot:
        return ""

    parts: list[str] = []
    labels = [
        ("perf_week", "1-week"),
        ("perf_1m", "1-month"),
        ("perf_3m", "3-month"),
        ("perf_6m", "6-month"),
        ("perf_year", "1-year"),
        ("perf_ytd", "YTD"),
    ]
    for key, label in labels:
        val = snapshot.get(key, 0.0)
        if val:
            parts.append(f"{label}: {val:+.1f}%")

    vol = snapshot.get("volatility_monthly", 0.0)
    if vol:
        parts.append(f"monthly volatility: {vol:.1f}%")

    return ", ".join(parts) if parts else ""
