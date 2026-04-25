"""
Market data tools for PSE (Philippine Stock Exchange) stocks.

**Façade module** — public entry point used by the agents and the rest of
the application. Each function transparently dispatches either to the
in-process domain service (default) or to the remote PH Stocks Advisor MCP
server when ``MCP_SERVER_URL`` is configured.

The function signatures, return types and exceptions are identical across
both paths, so callers never have to care which transport is in use.
"""

from __future__ import annotations

import logging

from ph_stocks_advisor.data.clients.dragonfi import (  # noqa: F401  re-exported
    SymbolNotFoundError,
)
from ph_stocks_advisor.data.mcp_client import get_client
from ph_stocks_advisor.data.models import (
    ControversyInfo,
    DividendInfo,
    FairValueEstimate,
    PriceMovement,
    SentimentInfo,
    StockPrice,
)

# Internal price-catalyst helper kept available for callers that import from
# this façade (no MCP dispatch — pure utility, not a network operation).
from ph_stocks_advisor.data.services.price import (  # noqa: F401
    detect_price_catalysts as _detect_price_catalysts,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_symbol(symbol: str) -> str:
    """Validate that *symbol* is a real PSE stock.

    Raises:
        SymbolNotFoundError: if the symbol is not listed on the PSE.
    """
    client = get_client()
    if client is not None:
        return str(client.call("validate_symbol", {"symbol": symbol}))

    from ph_stocks_advisor.data.clients.dragonfi import validate_pse_symbol

    return validate_pse_symbol(symbol)


# ---------------------------------------------------------------------------
# Data tools — each returns a typed Pydantic model
# ---------------------------------------------------------------------------


def fetch_stock_price(symbol: str) -> StockPrice:
    """Fetch current price snapshot for a PSE-listed stock."""
    client = get_client()
    if client is not None:
        payload = client.call("get_stock_price", {"symbol": symbol})
        return StockPrice.model_validate(payload)

    from ph_stocks_advisor.data.services.price import fetch_stock_price as _impl

    return _impl(symbol)


def fetch_dividend_info(symbol: str) -> DividendInfo:
    """Fetch dividend yield, payout ratio, sustainability and announcements."""
    client = get_client()
    if client is not None:
        payload = client.call("get_dividend_info", {"symbol": symbol})
        return DividendInfo.model_validate(payload)

    from ph_stocks_advisor.data.services.dividend import fetch_dividend_info as _impl

    return _impl(symbol)


def fetch_price_movement(symbol: str) -> PriceMovement:
    """Fetch 1-year price history, candlestick patterns and TV performance."""
    client = get_client()
    if client is not None:
        payload = client.call("get_price_movement", {"symbol": symbol})
        return PriceMovement.model_validate(payload)

    from ph_stocks_advisor.data.services.movement import fetch_price_movement as _impl

    return _impl(symbol)


def fetch_fair_value(symbol: str) -> FairValueEstimate:
    """Compute Graham-number fair-value estimate plus PE/PB ratios."""
    client = get_client()
    if client is not None:
        payload = client.call("get_fair_value", {"symbol": symbol})
        return FairValueEstimate.model_validate(payload)

    from ph_stocks_advisor.data.services.valuation import fetch_fair_value as _impl

    return _impl(symbol)


def fetch_controversy_info(symbol: str) -> ControversyInfo:
    """Detect price spikes, risk factors and gather recent news."""
    client = get_client()
    if client is not None:
        payload = client.call("get_controversy_info", {"symbol": symbol})
        return ControversyInfo.model_validate(payload)

    from ph_stocks_advisor.data.services.controversy import fetch_controversy_info as _impl

    return _impl(symbol)


def fetch_sentiment_info(symbol: str) -> SentimentInfo:
    """Gather macro / global-events sentiment context for a PSE stock."""
    client = get_client()
    if client is not None:
        payload = client.call("get_sentiment_info", {"symbol": symbol})
        return SentimentInfo.model_validate(payload)

    from ph_stocks_advisor.data.services.sentiment import fetch_sentiment_info as _impl

    return _impl(symbol)


__all__ = [
    "SymbolNotFoundError",
    "validate_symbol",
    "fetch_stock_price",
    "fetch_dividend_info",
    "fetch_price_movement",
    "fetch_fair_value",
    "fetch_controversy_info",
    "fetch_sentiment_info",
    "_detect_price_catalysts",
]
