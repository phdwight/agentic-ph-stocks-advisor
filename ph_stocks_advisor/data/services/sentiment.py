"""
Sentiment / global-events data service — gathers macro-level context
that may affect a PSE stock.

Single Responsibility: only handles global-events and sentiment data.
"""

from __future__ import annotations

import logging

from ph_stocks_advisor.data.models import SentimentInfo

logger = logging.getLogger(__name__)


def _fetch_profile_context(symbol: str) -> tuple[str, bool]:
    """Best-effort (sector, is_reit) lookup via DragonFi — one profile call.

    ``is_reit`` must reach the agent payload explicitly (never inferred by
    the LLM — see the REIT-classification contract).
    """
    try:
        from ph_stocks_advisor.data.clients.dragonfi import fetch_stock_profile

        profile = fetch_stock_profile(symbol)
        if not profile:
            return "", False
        return str(profile.get("sector", "")), bool(profile.get("isREIT", False))
    except Exception:
        return "", False


def _fetch_bsp_rate() -> str:
    """Fetch BSP policy-rate / bond-yield context from Tavily (empty-safe)."""
    try:
        from ph_stocks_advisor.data.clients.tavily_search import search_bsp_rate

        return search_bsp_rate()
    except Exception as exc:
        logger.debug("BSP rate search unavailable: %s", exc)
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
    sector, is_reit = _fetch_profile_context(symbol)
    global_news = _fetch_global_events_news(symbol)
    bsp_rate = _fetch_bsp_rate()

    return SentimentInfo(
        symbol=symbol,
        global_events_news=global_news,
        sector=sector,
        bsp_rate=bsp_rate,
        is_reit=is_reit,
    )
