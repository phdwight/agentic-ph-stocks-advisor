"""
FastMCP server wiring for the PH Stocks Advisor data tools.

Tools are thin wrappers around the existing domain services in
``ph_stocks_advisor.data.services`` — no logic is duplicated. Returned
objects are Pydantic models which FastMCP serialises to structured JSON.

Validation errors (``SymbolNotFoundError``) are surfaced as MCP tool errors
with a structured payload so the client can re-raise the original exception
type.
"""

from __future__ import annotations

import asyncio
import logging
import os

from mcp.server.fastmcp import FastMCP

from ph_stocks_advisor.data.clients.dragonfi import (
    SymbolNotFoundError,
    validate_pse_symbol,
)
from ph_stocks_advisor.data.models import (
    ControversyInfo,
    DividendInfo,
    FairValueEstimate,
    PriceMovement,
    SentimentInfo,
    StockPrice,
)
from ph_stocks_advisor.data.services.controversy import fetch_controversy_info
from ph_stocks_advisor.data.services.dividend import fetch_dividend_info
from ph_stocks_advisor.data.services.movement import fetch_price_movement
from ph_stocks_advisor.data.services.price import fetch_stock_price
from ph_stocks_advisor.data.services.sentiment import fetch_sentiment_info
from ph_stocks_advisor.data.services.valuation import fetch_fair_value

logger = logging.getLogger(__name__)


# Marker token recognised by the MCP client to convert tool errors back into
# ``SymbolNotFoundError`` instead of generic ``RuntimeError``.
_SYMBOL_NOT_FOUND_PREFIX = "SymbolNotFoundError: "


def build_server() -> FastMCP:
    """Construct the FastMCP server with all advisor data tools registered."""
    host = os.getenv("MCP_HOST", "0.0.0.0")  # noqa: S104  bind-all is intended inside containers
    port = int(os.getenv("MCP_PORT", "8000"))

    mcp = FastMCP(
        name="ph-stocks-advisor",
        instructions=(
            "Data tools for Philippine Stock Exchange (PSE) listed equities. "
            "Each tool returns a structured snapshot for one analysis dimension."
        ),
        host=host,
        port=port,
    )

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    @mcp.tool()
    async def validate_symbol(symbol: str) -> str:
        """Return the canonical PSE stock code for *symbol*.

        Raises an MCP tool error prefixed with ``SymbolNotFoundError:`` when
        the ticker is not listed on the PSE.
        """
        logger.info("MCP tool called: validate_symbol(symbol=%r)", symbol)
        try:
            result = await asyncio.to_thread(validate_pse_symbol, symbol)
        except SymbolNotFoundError as exc:
            logger.info("MCP tool validate_symbol rejected symbol=%r: %s", symbol, exc)
            raise ValueError(f"{_SYMBOL_NOT_FOUND_PREFIX}{exc}") from exc
        logger.info("MCP tool validate_symbol resolved symbol=%r -> %s", symbol, result)
        return result

    # ------------------------------------------------------------------
    # Data tools — each returns a Pydantic model
    # ------------------------------------------------------------------
    @mcp.tool()
    async def get_stock_price(symbol: str) -> StockPrice:
        """Return current price snapshot vs. 52-week range for a PSE stock."""
        logger.info("MCP tool called: get_stock_price(symbol=%r)", symbol)
        return await asyncio.to_thread(fetch_stock_price, symbol)

    @mcp.tool()
    async def get_dividend_info(symbol: str) -> DividendInfo:
        """Return dividend yield, payout ratio, sustainability and announcements."""
        logger.info("MCP tool called: get_dividend_info(symbol=%r)", symbol)
        return await asyncio.to_thread(fetch_dividend_info, symbol)

    @mcp.tool()
    async def get_price_movement(symbol: str) -> PriceMovement:
        """Return 1-year price movement, candlestick patterns and TV performance."""
        logger.info("MCP tool called: get_price_movement(symbol=%r)", symbol)
        return await asyncio.to_thread(fetch_price_movement, symbol)

    @mcp.tool()
    async def get_fair_value(symbol: str) -> FairValueEstimate:
        """Return Graham-number fair-value estimate plus PE/PB ratios."""
        logger.info("MCP tool called: get_fair_value(symbol=%r)", symbol)
        return await asyncio.to_thread(fetch_fair_value, symbol)

    @mcp.tool()
    async def get_controversy_info(symbol: str) -> ControversyInfo:
        """Return detected price spikes, risk factors and recent news headlines."""
        logger.info("MCP tool called: get_controversy_info(symbol=%r)", symbol)
        return await asyncio.to_thread(fetch_controversy_info, symbol)

    @mcp.tool()
    async def get_sentiment_info(symbol: str) -> SentimentInfo:
        """Return macro / global-events sentiment context for a PSE stock."""
        logger.info("MCP tool called: get_sentiment_info(symbol=%r)", symbol)
        return await asyncio.to_thread(fetch_sentiment_info, symbol)

    return mcp
