"""
Price movement data service — fetches historical price data and computes
movement metrics for PSE stocks.

Single Responsibility: only handles price movement retrieval, trend
classification, and supplementary data assembly (candlestick patterns,
TradingView performance, web news).
"""

from __future__ import annotations

import logging

import pandas as pd

from ph_stocks_advisor.data.candlestick import analyse_candlesticks
from ph_stocks_advisor.data.dragonfi import fetch_stock_profile
from ph_stocks_advisor.data.models import PriceMovement, TrendDirection
from ph_stocks_advisor.data.price_service import detect_price_catalysts
from ph_stocks_advisor.data.pse_edge import fetch_pse_edge_ohlcv
from ph_stocks_advisor.data.tavily_search import search_stock_news
from ph_stocks_advisor.data.tradingview import (
    fetch_tradingview_snapshot,
    format_tv_performance_summary,
)

from ph_stocks_advisor.infra.config import get_settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _classify_trend(change_pct: float) -> TrendDirection:
    """Classify a percentage change into a trend direction."""
    s = get_settings()
    if change_pct > s.trend_up_threshold:
        return TrendDirection.UPTREND
    if change_pct < s.trend_down_threshold:
        return TrendDirection.DOWNTREND
    return TrendDirection.SIDEWAYS


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_price_movement(symbol: str) -> PriceMovement:
    """Fetch 1-year price history and compute movement metrics.

    **Primary**: PSE EDGE daily OHLCV (covers all PSE-listed securities).
    **Fallback**: DragonFi 52-week range + TradingView performance.
    """
    symbol = symbol.upper().replace(".PS", "")

    # Try PSE EDGE (most reliable for PSE)
    hist = fetch_pse_edge_ohlcv(symbol)

    # Fetch profile once — used for catalysts in all branches
    profile = fetch_stock_profile(symbol)
    catalysts = detect_price_catalysts(profile) if profile else []

    # Tavily web news for movement context
    company_name = profile.get("companyName", "") if profile else ""
    web_news = search_stock_news(symbol, company_name=company_name)

    if not hist.empty:
        closes = hist["Close"].tolist()
        year_start = closes[0]
        year_end = closes[-1]
        year_change_pct = ((year_end - year_start) / year_start) * 100 if year_start else 0
        max_price = max(closes)
        min_price = min(closes)

        returns = hist["Close"].pct_change().dropna()
        volatility = float(returns.std() * 100) if len(returns) > 1 else 0.0

        # Max drawdown: largest peak-to-trough decline
        cummax = hist["Close"].cummax()
        drawdown = (hist["Close"] - cummax) / cummax * 100
        max_drawdown_pct = round(float(drawdown.min()), 2) if len(drawdown) > 0 else 0.0

        # Candlestick pattern analysis (uses full OHLCV)
        candle_summary = analyse_candlesticks(hist)
        candlestick_patterns = candle_summary.to_text()

        # TradingView multi-period performance (supplementary)
        tv = fetch_tradingview_snapshot(symbol)
        perf_summary = format_tv_performance_summary(tv)

        monthly = hist["Close"].resample("ME").mean()
        monthly_prices = [round(float(p), 2) for p in monthly.tolist()]

        trend = _classify_trend(year_change_pct)

        return PriceMovement(
            symbol=symbol,
            year_start_price=round(year_start, 2),
            year_end_price=round(year_end, 2),
            year_change_pct=round(year_change_pct, 2),
            max_price=round(max_price, 2),
            min_price=round(min_price, 2),
            volatility=round(volatility, 4),
            max_drawdown_pct=max_drawdown_pct,
            trend=trend,
            monthly_prices=monthly_prices,
            price_catalysts=catalysts,
            candlestick_patterns=candlestick_patterns,
            performance_summary=perf_summary,
            web_news=web_news,
        )

    # Fallback: use DragonFi 52-week range + TradingView performance data
    logger.info("No OHLCV history for %s — using DragonFi + TradingView", symbol)

    # TradingView gives accurate multi-period performance & volatility
    tv = fetch_tradingview_snapshot(symbol)
    perf_summary = format_tv_performance_summary(tv)

    if profile:
        high52 = float(profile.get("weekHigh52", 0) or 0)
        low52 = float(profile.get("weekLow52", 0) or 0)
        current = float(profile.get("price", 0) or 0)

        # Use TradingView's 1-year performance if available (more accurate
        # than computing from 52-week low which is misleading)
        tv_year_pct = tv.get("perf_year", 0.0)
        if tv_year_pct:
            change_pct = round(tv_year_pct, 2)
        else:
            change_pct = (
                round(((current - low52) / low52) * 100, 2) if low52 > 0 else 0.0
            )

        trend = _classify_trend(change_pct)

        # Use TradingView monthly volatility if available
        tv_volatility = tv.get("volatility_monthly", 0.0)

        return PriceMovement(
            symbol=symbol,
            year_start_price=round(current / (1 + tv_year_pct / 100), 2) if tv_year_pct else low52,
            year_end_price=current,
            year_change_pct=change_pct,
            max_price=high52,
            min_price=low52,
            volatility=round(tv_volatility, 4),
            max_drawdown_pct=round(((low52 - high52) / high52) * 100, 2) if high52 > 0 else 0.0,
            trend=trend,
            monthly_prices=[],
            price_catalysts=catalysts,
            performance_summary=perf_summary,
            web_news=web_news,
        )

    return PriceMovement(symbol=symbol)
