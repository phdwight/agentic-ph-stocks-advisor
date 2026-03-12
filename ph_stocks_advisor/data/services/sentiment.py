"""
Sentiment / global-events data service — gathers macro-level context
that may affect a PSE stock.

Single Responsibility: only handles global-events and sentiment data.
"""

from __future__ import annotations

import logging

from ph_stocks_advisor.data.models import SentimentInfo

logger = logging.getLogger(__name__)


def _fetch_sector(symbol: str) -> str:
    """Best-effort sector lookup via DragonFi."""
    try:
        from ph_stocks_advisor.data.clients.dragonfi import fetch_stock_profile

        profile = fetch_stock_profile(symbol)
        return str(profile.get("sector", "")) if profile else ""
    except Exception:
        return ""


def _fetch_global_events_news(symbol: str) -> str:
    """Fetch global events news from Tavily (gracefully returns empty)."""
    try:
        from ph_stocks_advisor.data.clients.tavily_search import search_global_events

        return search_global_events(symbol)
    except Exception as exc:
        logger.debug("Global events search unavailable for %s: %s", symbol, exc)
        return ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_sentiment_info(symbol: str) -> SentimentInfo:
    """Gather global-events and macro-sentiment context for *symbol*.

    Combines sector information with a Tavily search for global events
    that may affect the Philippine market.  The LLM agent will further
    enrich the data via its tool-calling capability.
    """
    symbol = symbol.upper().replace(".PS", "")
    sector = _fetch_sector(symbol)
    global_news = _fetch_global_events_news(symbol)

    return SentimentInfo(
        symbol=symbol,
        global_events_news=global_news,
        sector=sector,
    )
