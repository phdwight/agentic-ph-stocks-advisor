"""
LangChain tool definitions for web search via Tavily.

These tools are bound to specialist agents so the LLM can autonomously
decide whether to invoke a web search for additional context.

Single Responsibility: only defines tool wrappers — no analysis logic.
Dependency Inversion: tools depend on the Tavily client abstraction
(which gracefully degrades when ``TAVILY_API_KEY`` is not set).
"""

from __future__ import annotations

import logging

from langchain_core.tools import tool

from ph_stocks_advisor.data.clients.tavily_search import (
    search_dividend_news as _tavily_dividend_news,
    search_stock_controversies as _tavily_controversies,
    search_stock_news as _tavily_stock_news,
)

logger = logging.getLogger(__name__)


def _company_name(symbol: str) -> str:
    """Best-effort lookup of company name for richer search queries."""
    try:
        from ph_stocks_advisor.data.clients.dragonfi import fetch_stock_profile

        profile = fetch_stock_profile(symbol)
        return str(profile.get("companyName", "")) if profile else ""
    except Exception:
        return ""


@tool
def search_dividend_news(symbol: str) -> str:
    """Search the web for recent dividend announcements, declarations,
    ex-dates, and payout amounts for a Philippine Stock Exchange (PSE) stock.

    Use this tool when you need additional context about recent or
    upcoming dividend events that is not already in the data provided.
    """
    return _tavily_dividend_news(symbol, company_name=_company_name(symbol))


@tool
def search_stock_news(symbol: str) -> str:
    """Search the web for recent news, analyst coverage, and corporate
    events for a Philippine Stock Exchange (PSE) stock.

    Use this tool when you need additional context about recent
    developments, price drivers, or market sentiment that may explain
    price movements or other patterns in the data.
    """
    return _tavily_stock_news(symbol, company_name=_company_name(symbol))


@tool
def search_stock_controversies(symbol: str) -> str:
    """Search the web for controversies, regulatory issues, SEC filings,
    legal disputes, or other negative events for a PSE stock.

    Use this tool when you need to investigate potential risk factors
    or corporate governance concerns not already evident in the data.
    """
    return _tavily_controversies(symbol, company_name=_company_name(symbol))
