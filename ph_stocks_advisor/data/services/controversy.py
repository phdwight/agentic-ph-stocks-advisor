"""
Controversy / risk data service — detects price anomalies and gathers
news for PSE stocks.

Single Responsibility: only handles controversy and risk data retrieval.
"""

from __future__ import annotations

import logging

import pandas as pd

from ph_stocks_advisor.data.clients.dragonfi import fetch_stock_news
from ph_stocks_advisor.data.clients.pse_edge import (
    fetch_pse_edge_ohlcv,
    fetch_suspension_info,
)
from ph_stocks_advisor.data.models import ControversyInfo, SuspensionInfo
from ph_stocks_advisor.infra.config import get_settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _fetch_history(symbol: str) -> pd.DataFrame:
    """Fetch ~1-year daily OHLCV history from PSE EDGE.

    Returns a DataFrame (possibly empty) and never raises.
    """
    try:
        return fetch_pse_edge_ohlcv(symbol, days=365)
    except Exception as exc:  # pragma: no cover
        logger.debug("PSE EDGE history unavailable for %s: %s", symbol, exc)
        return pd.DataFrame()


def _fetch_suspension(symbol: str) -> SuspensionInfo:
    """Fetch PSE EDGE trading status and suspension details (empty-safe)."""
    try:
        return fetch_suspension_info(symbol)
    except Exception as exc:  # pragma: no cover
        logger.debug("PSE EDGE suspension info unavailable for %s: %s", symbol, exc)
        return SuspensionInfo(symbol=symbol)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fetch_controversy_info(symbol: str) -> ControversyInfo:
    """Detect potential price anomalies and gather recent news.

    Uses PSE EDGE OHLCV for daily history (spike detection), PSE EDGE
    trading status / suspension disclosures, and DragonFi for recent news
    headlines.
    """
    symbol = symbol.upper().replace(".PS", "")
    hist = _fetch_history(symbol)
    suspension = _fetch_suspension(symbol)
    spikes: list[str] = []
    risk_factors: list[str] = []

    suspension_details = ""
    if suspension.is_suspended:
        suspension_details = suspension.to_summary()
        risk_factors.append(suspension_details)

    if not hist.empty:
        returns = hist["Close"].pct_change().dropna()
        std_ret = returns.std()

        s = get_settings()
        for date, ret in returns.items():
            if bool(abs(ret) > s.spike_std_multiplier * std_ret) and bool(abs(ret) > s.spike_min_abs_return):
                direction = "spike up" if bool(ret > 0) else "spike down"
                spikes.append(f"{date.strftime('%Y-%m-%d')}: {direction} of {ret * 100:.1f}%")  # type: ignore[union-attr]

        if bool(std_ret > s.high_volatility_threshold):
            risk_factors.append(f"High daily volatility (std > {s.high_volatility_threshold * 100:.0f}%)")

        avg_price = hist["Close"].mean()
        last_price = hist["Close"].iloc[-1]
        if last_price > avg_price * s.overvaluation_multiplier:
            over_pct = round((s.overvaluation_multiplier - 1) * 100)
            risk_factors.append(f"Current price is >{over_pct}% above 52-week average — potential overvaluation")
        elif last_price < avg_price * s.distress_multiplier:
            under_pct = round((1 - s.distress_multiplier) * 100)
            risk_factors.append(f"Current price is >{under_pct}% below 52-week average — potential distress")

    # Fetch recent news from DragonFi
    news_items = fetch_stock_news(symbol, page_size=5)
    if news_items:
        headlines = []
        for item in news_items:
            title = item.get("title") or item.get("headline", "")
            source = item.get("source", "")
            if title:
                headlines.append(f"[{source}] {title}" if source else title)
        news_summary = "\n".join(headlines) if headlines else "No recent news found."
    else:
        news_summary = "No recent news available from DragonFi."

    if suspension.is_suspended and suspension_details:
        notice_line = f"[PSE EDGE Official Notice] {suspension_details}"
        news_summary = notice_line if news_summary.startswith("No recent news") else f"{notice_line}\n{news_summary}"

    return ControversyInfo(
        symbol=symbol,
        sudden_spikes=spikes,
        risk_factors=risk_factors,
        recent_news_summary=news_summary,
        is_suspended=suspension.is_suspended,
        trading_status=suspension.trading_status,
        suspension_details=suspension_details,
    )
