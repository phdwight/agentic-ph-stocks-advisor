"""
Controversy / risk data service — detects price anomalies and gathers
news for PSE stocks.

Single Responsibility: only handles controversy and risk data retrieval.
"""

from __future__ import annotations

import logging

import pandas as pd

from ph_stocks_advisor.data.dragonfi import (
    fetch_stock_news,
    fetch_stock_profile,
)
from ph_stocks_advisor.data.models import ControversyInfo
from ph_stocks_advisor.data.pse_edge import fetch_pse_edge_ohlcv
from ph_stocks_advisor.data.tavily_search import (
    search_stock_controversies,
    search_stock_news,
)
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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_controversy_info(symbol: str) -> ControversyInfo:
    """Detect potential price anomalies and gather recent news.

    Uses PSE EDGE OHLCV for daily history (spike detection) and DragonFi
    for recent news headlines.
    """
    symbol = symbol.upper().replace(".PS", "")
    hist = _fetch_history(symbol)
    spikes: list[str] = []
    risk_factors: list[str] = []

    if not hist.empty:
        returns = hist["Close"].pct_change().dropna()
        std_ret = returns.std()

        s = get_settings()
        for date, ret in returns.items():
            if abs(ret) > s.spike_std_multiplier * std_ret and abs(ret) > s.spike_min_abs_return:
                direction = "spike up" if ret > 0 else "spike down"
                spikes.append(
                    f"{date.strftime('%Y-%m-%d')}: {direction} of {ret*100:.1f}%"
                )

        if std_ret > s.high_volatility_threshold:
            risk_factors.append(
                f"High daily volatility (std > {s.high_volatility_threshold*100:.0f}%)"
            )

        avg_price = hist["Close"].mean()
        last_price = hist["Close"].iloc[-1]
        if last_price > avg_price * s.overvaluation_multiplier:
            over_pct = round((s.overvaluation_multiplier - 1) * 100)
            risk_factors.append(
                f"Current price is >{over_pct}% above 52-week average — potential overvaluation"
            )
        elif last_price < avg_price * s.distress_multiplier:
            under_pct = round((1 - s.distress_multiplier) * 100)
            risk_factors.append(
                f"Current price is >{under_pct}% below 52-week average — potential distress"
            )

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

    # Web search via Tavily for richer news coverage
    profile = fetch_stock_profile(symbol)
    company_name = str(profile.get("companyName", "")) if profile else ""
    web_general = search_stock_news(symbol, company_name=company_name)
    web_controversy = search_stock_controversies(symbol, company_name=company_name)
    web_news = ""
    if web_general and not web_general.startswith("No "):
        web_news += f"**Recent Web News:**\n{web_general}"
    if web_controversy and not web_controversy.startswith("No "):
        if web_news:
            web_news += "\n\n"
        web_news += f"**Controversy Search:**\n{web_controversy}"
    if not web_news:
        web_news = "No web news available (Tavily API key may not be configured)."

    return ControversyInfo(
        symbol=symbol,
        sudden_spikes=spikes,
        risk_factors=risk_factors,
        recent_news_summary=news_summary,
        web_news=web_news,
    )
