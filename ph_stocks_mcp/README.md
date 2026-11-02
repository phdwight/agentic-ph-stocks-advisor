# ph_stocks_mcp

## Overview
Model Context Protocol (MCP) server package for the PH Stocks Advisor. It adapts the advisor's Philippine Stock Exchange (PSE) symbol validation and data-fetching domain services into FastMCP tools served over Streamable HTTP, enabling consumption by the advisor application or any MCP-compatible client over the network without duplicating domain logic.

## Interface
- `build_server() -> FastMCP` (`[__init__.py:__all__]`, `[server.py:build_server]`): Constructs and returns the `ph-stocks-advisor` `FastMCP` server instance with all advisor data tools registered.
- `main() -> None` (`[__main__.py:main]`): CLI entry point (`python -m ph_stocks_mcp`) that initializes logging via `configure_logging()` and runs the FastMCP server with `transport="streamable-http"`.
- Registered MCP Tools (`[server.py:build_server]`):
  - `validate_symbol(symbol: str) -> str` (`[server.py:validate_symbol]`): Validates a ticker via `validate_pse_symbol` and returns its canonical PSE stock code.
  - `get_stock_price(symbol: str) -> StockPrice` (`[server.py:get_stock_price]`): Returns current price snapshot vs. 52-week range via `fetch_stock_price`.
  - `get_dividend_info(symbol: str) -> DividendInfo` (`[server.py:get_dividend_info]`): Returns dividend yield, payout ratio, sustainability, and announcements via `fetch_dividend_info`.
  - `get_price_movement(symbol: str) -> PriceMovement` (`[server.py:get_price_movement]`): Returns 1-year price movement, candlestick patterns, and TradingView performance via `fetch_price_movement`.
  - `get_fair_value(symbol: str) -> FairValueEstimate` (`[server.py:get_fair_value]`): Returns Graham-number fair-value estimate and PE/PB ratios via `fetch_fair_value`.
  - `get_controversy_info(symbol: str) -> ControversyInfo` (`[server.py:get_controversy_info]`): Returns detected price spikes, risk factors, and recent news headlines via `fetch_controversy_info`.
  - `get_sentiment_info(symbol: str) -> SentimentInfo` (`[server.py:get_sentiment_info]`): Returns macro and global-events sentiment context via `fetch_sentiment_info`.

## Invariants
- **Thin Protocol Adapter** (`[__init__.py]`, `[server.py]`): Contains no domain or business logic of its own; every MCP tool delegates directly to existing services in [`ph_stocks_advisor/data/services`](../ph_stocks_advisor/data/services/README.md) and [`ph_stocks_advisor/data/clients`](../ph_stocks_advisor/data/clients/README.md) using `asyncio.to_thread`.
- **Error Marker Contract** (`[server.py:_SYMBOL_NOT_FOUND_PREFIX]`, `[server.py:validate_symbol]`): `SymbolNotFoundError` exceptions raised during symbol validation must be re-raised as `ValueError` prefixed with `"SymbolNotFoundError: "` so MCP clients can reconstruct `SymbolNotFoundError`, whereas transient `SymbolValidationUnavailableError` exceptions must be re-raised as `ValueError` without that prefix so clients treat them as retryable failures.

## Side Effects
- **Environment Configuration** (`[server.py:build_server]`): Reads `MCP_HOST` (defaults to `"0.0.0.0"`) and `MCP_PORT` (defaults to `"8000"`) from environment variables.
- **Network Listener & Logging** (`[__main__.py:main]`, `[server.py]`): Configures process logging via `configure_logging()`, emits structured `logging` info/warning messages on tool invocation, and binds an HTTP server when started via `server.run(transport="streamable-http")`.
- **Upstream Data Fetching** (`[server.py]`): Invoking registered tools triggers outbound network requests through the underlying `ph_stocks_advisor.data` services and clients.

## Verification
- Run: `pytest`
