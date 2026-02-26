"""
Tavily web search integration for real-time news and announcements.

Provides stock-specific web searches for Philippine Stock Exchange
securities — dividend declarations, corporate announcements, analyst
coverage, and controversy/risk news.

The module gracefully degrades when the ``TAVILY_API_KEY`` environment
variable is not set: all search functions return empty results instead
of raising exceptions.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def _get_api_key() -> str:
    """Lazily read the Tavily API key so ``load_dotenv()`` has time to run."""
    return os.getenv("TAVILY_API_KEY", "")


def _get_client():
    """Return a TavilyClient if the API key is configured, else None."""
    api_key = _get_api_key()
    if not api_key:
        logger.debug("TAVILY_API_KEY not set — web search disabled")
        return None
    try:
        from tavily import TavilyClient  # type: ignore[import-untyped]

        return TavilyClient(api_key=api_key)
    except Exception as exc:
        logger.warning("Failed to initialise TavilyClient: %s", exc)
        return None


def _search(
    query: str,
    *,
    max_results: int = 5,
    search_depth: str = "basic",
    include_domains: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Run a Tavily search and return a list of result dicts.

    Each result dict contains ``title``, ``url``, ``content`` (snippet),
    and ``score``.  Returns an empty list on any failure.
    """
    client = _get_client()
    if client is None:
        return []
    try:
        params: dict[str, Any] = {
            "query": query,
            "max_results": max_results,
            "search_depth": search_depth,
        }
        if include_domains:
            params["include_domains"] = include_domains
        response = client.search(**params)
        return response.get("results", [])
    except Exception as exc:
        logger.warning("Tavily search failed for %r: %s", query, exc)
        return []


# ---------------------------------------------------------------------------
# Stock-specific search helpers
# ---------------------------------------------------------------------------

def search_dividend_news(symbol: str, company_name: str = "") -> str:
    """Search the web for recent dividend announcements for a PSE stock.

    Returns a formatted string of search results (or a fallback message
    if no results are found / Tavily is not configured).
    """
    name_part = f" ({company_name})" if company_name else ""
    query = (
        f"{symbol}{name_part} Philippine stock dividend announcement "
        f"declaration ex-date 2025 OR 2026"
    )
    results = _search(query, max_results=5)
    return _format_results(results, fallback="No recent dividend news found via web search.")


def search_stock_news(symbol: str, company_name: str = "") -> str:
    """Search the web for recent news, analyst coverage, and events.

    Returns a formatted string of search results.
    """
    name_part = f" ({company_name})" if company_name else ""
    query = (
        f"{symbol}{name_part} Philippine stock PSE latest news 2025 OR 2026"
    )
    results = _search(query, max_results=5)
    return _format_results(results, fallback="No recent news found via web search.")


def search_stock_controversies(symbol: str, company_name: str = "") -> str:
    """Search the web for controversies, regulatory issues, or negative events.

    Returns a formatted string of search results.
    """
    name_part = f" ({company_name})" if company_name else ""
    query = (
        f"{symbol}{name_part} Philippine stock controversy risk issue "
        f"SEC regulatory concern"
    )
    results = _search(query, max_results=3, search_depth="basic")
    return _format_results(results, fallback="No controversies found via web search.")


def _format_results(results: list[dict[str, Any]], fallback: str = "") -> str:
    """Format Tavily search results into a readable string."""
    if not results:
        return fallback
    lines: list[str] = []
    for r in results:
        title = r.get("title", "")
        url = r.get("url", "")
        snippet = r.get("content", "")
        if title:
            lines.append(f"• {title}")
            if snippet:
                # Trim long snippets
                lines.append(f"  {snippet[:300]}")
            if url:
                lines.append(f"  Source: {url}")
    return "\n".join(lines) if lines else fallback
