"""
Market data tools for PSE (Philippine Stock Exchange) stocks.

**Façade module** — public entry point used by the agents and the rest of
the application. Every call dispatches to the PH Stocks Advisor MCP server
through :func:`ph_stocks_advisor.data.mcp_client.get_client`. There is no
in-process fallback: ``MCP_SERVER_URL`` must be configured.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

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
# this façade (pure utility, not a network operation).
from ph_stocks_advisor.data.services.price import (  # noqa: F401
    detect_price_catalysts as _detect_price_catalysts,
)

logger = logging.getLogger(__name__)


def _call(tool_name: str, symbol: str) -> Any:
    """Invoke an MCP tool with a single ``symbol`` argument."""
    return get_client().call(tool_name, {"symbol": symbol})


def _model_call[M: BaseModel](tool_name: str, symbol: str, model: type[M]) -> M:
    """Invoke an MCP tool and validate its payload as *model*."""
    return model.model_validate(_call(tool_name, symbol))


# ---------------------------------------------------------------------------
# Public tools
# ---------------------------------------------------------------------------


def validate_symbol(symbol: str) -> str:
    """Validate that *symbol* is a real PSE stock.

    Raises:
        SymbolNotFoundError: if the symbol is not listed on the PSE.
    """
    return str(_call("validate_symbol", symbol))


def fetch_stock_price(symbol: str) -> StockPrice:
    """Fetch current price snapshot for a PSE-listed stock."""
    return _model_call("get_stock_price", symbol, StockPrice)


def fetch_dividend_info(symbol: str) -> DividendInfo:
    """Fetch dividend yield, payout ratio, sustainability and announcements."""
    return _model_call("get_dividend_info", symbol, DividendInfo)


def fetch_price_movement(symbol: str) -> PriceMovement:
    """Fetch 1-year price history, candlestick patterns and TV performance."""
    return _model_call("get_price_movement", symbol, PriceMovement)


def fetch_fair_value(symbol: str) -> FairValueEstimate:
    """Compute Graham-number fair-value estimate plus PE/PB ratios."""
    return _model_call("get_fair_value", symbol, FairValueEstimate)


def fetch_controversy_info(symbol: str) -> ControversyInfo:
    """Detect price spikes, risk factors and gather recent news."""
    return _model_call("get_controversy_info", symbol, ControversyInfo)


def fetch_sentiment_info(symbol: str) -> SentimentInfo:
    """Gather macro / global-events sentiment context for a PSE stock."""
    return _model_call("get_sentiment_info", symbol, SentimentInfo)


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
