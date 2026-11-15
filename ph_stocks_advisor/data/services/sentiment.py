"""
Sentiment / global-events data service — gathers macro-level context
that may affect a PSE stock.

Single Responsibility: only handles global-events and sentiment data.
"""

from __future__ import annotations

import contextlib
import logging

from ph_stocks_advisor.data.models import BSPKeyRates, SentimentInfo

logger = logging.getLogger(__name__)


def _fetch_profile_context(symbol: str) -> tuple[str, bool]:
    """Best-effort (sector, is_reit) lookup via DragonFi — one profile call.

    ``is_reit`` must reach the agent payload explicitly (never inferred by
    the LLM — see the REIT-classification contract).
    """
    with contextlib.suppress(Exception):
        from ph_stocks_advisor.data.clients.dragonfi import fetch_stock_profile

        profile = fetch_stock_profile(symbol)
        if profile:
            return str(profile.get("sector", "")), bool(profile.get("isREIT", False))
    # DragonFi outage — REIT status from the PSE EDGE company registry
    # (registry data, never LLM-inferred); unknown maps to False.
    try:
        from ph_stocks_advisor.data.clients.pse_edge import is_reit_from_edge

        return "", is_reit_from_edge(symbol) is True
    except Exception:
        return "", False


def _fetch_official_bsp_rates() -> BSPKeyRates:
    """Fetch official BSP key interest rates and USD/PHP reference rate (empty-safe)."""
    try:
        from ph_stocks_advisor.data.clients.bsp import fetch_bsp_key_rates

        return fetch_bsp_key_rates()
    except Exception as exc:
        logger.debug("Official BSP key rates unavailable: %s", exc)
        return BSPKeyRates()


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

    Combines sector information, official BSP Key Rates (policy interest
    rates, inflation, and USD/PHP reference rate), and Tavily searches for
    global events and PH bond-yield context.
    """
    symbol = symbol.upper().replace(".PS", "")
    sector, is_reit = _fetch_profile_context(symbol)
    global_news = _fetch_global_events_news(symbol)
    official_bsp = _fetch_official_bsp_rates()
    tavily_bsp = _fetch_bsp_rate()

    bsp_rate = "\n\n".join(part for part in (official_bsp.summary, tavily_bsp) if part)
    peso_usd_rate = ""
    if official_bsp.usd_php_rate is not None:
        date_suffix = f" (as of {official_bsp.as_of_date})" if official_bsp.as_of_date else ""
        peso_usd_rate = f"₱{official_bsp.usd_php_rate:.4f} per US$1.00{date_suffix}"

    return SentimentInfo(
        symbol=symbol,
        global_events_news=global_news,
        sector=sector,
        peso_usd_rate=peso_usd_rate,
        bsp_rate=bsp_rate,
        is_reit=is_reit,
    )
