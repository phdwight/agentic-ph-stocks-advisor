# data

## Overview
Data layer package providing Pydantic domain models, LangGraph shared state (`AdvisorState`), a thread-safe synchronous MCP client (`_SyncMCPClient`), and a market-data tool façade (`tools.py`) that dispatches agent queries to the PH Stocks Advisor MCP server, alongside subpackages for external API clients, domain orchestration services, and candlestick analysis.

## Interface
- `[models.py:Verdict]` - `StrEnum` with values `BUY` (`'BUY'`) and `NOT_BUY` (`'NOT BUY'`) representing the final investment verdict.
- `[models.py:TrendDirection]` - `StrEnum` with values `UPTREND` (`'uptrend'`), `DOWNTREND` (`'downtrend'`), and `SIDEWAYS` (`'sideways'`).
- `[models.py:StockPrice]` - Pydantic model holding current/historical price metrics and detected `price_catalysts`.
- `[models.py:DividendAnnouncement]` - Pydantic model for a single PSE EDGE dividend declaration with `to_summary()` helper.
- `[models.py:DividendInfo]` - Pydantic model grouping core dividend metrics, DragonFi financial trends/`fcf_payout_ratio`, and external news/announcements.
- `[models.py:PriceMovement]` - Pydantic model for 1-year price statistics, drawdown, daily close series (`monthly_prices`), trend direction, catalysts, candlestick patterns, and TradingView performance.
- `[models.py:FairValueEstimate]` - Pydantic model for valuation metrics (`pe_ratio`, `pb_ratio`, `peg_ratio`, `forward_pe`, `book_value`, `estimated_fair_value`, `discount_pct`, `is_reit`).
- `[models.py:ControversyInfo]` - Pydantic model for sudden price spikes, risk factors, and controversy news.
- `[models.py:SentimentInfo]` - Pydantic model for global events news, sector, PHP/USD rate, BSP rate, and REIT status.
- `[models.py:PriceAnalysis]`, `[models.py:DividendAnalysis]`, `[models.py:MovementAnalysis]`, `[models.py:ValuationAnalysis]`, `[models.py:ControversyAnalysis]`, `[models.py:SentimentAnalysis]` - Agent output wrappers pairing domain data models with markdown analysis strings.
- `[models.py:ConsolidationResponse]` - Structured LLM output schema for the consolidator agent with verdict, justification, markdown summary, and six optional 0–100 sub-scores.
- `[models.py:SCORE_BANDS]` - List of `(lower_bound, label)` tuples mapping 0–100 verdict scores to display bands (`STRONG BUY`, `BUY`, `WAIT`, `DON'T BUY`, `AVOID`).
- `[models.py:score_band]` - Maps a 0–100 integer score to its display band string using `SCORE_BANDS`.
- `[models.py:FinalReport]` - Consolidated investment report model containing symbol, verdict, summary, optional score, per-section narratives, and `movement_monthly_prices`.
- `[models.py:AdvisorState]` - Shared LangGraph workflow state holding `symbol`, `data_gaps`, per-agent analysis fields, and `final_report`.
- `[mcp_client.py:MCPClientError]` - `RuntimeError` subclass raised when an MCP tool call or session initialization fails for non-business reasons.
- `[mcp_client.py:MCPNotConfiguredError]` - `RuntimeError` subclass raised when `MCP_SERVER_URL` is not configured.
- `[mcp_client.py:_SyncMCPClient]` - Thread-safe synchronous wrapper over an async MCP `ClientSession` exposing `call(tool_name, arguments)` and `close()`.
- `[mcp_client.py:get_client]` - Module-level singleton accessor returning the shared `_SyncMCPClient` configured via `settings.mcp_server_url` or an explicit URL.
- `[mcp_client.py:reset_client]` - Closes and clears the module-level `_SyncMCPClient` singleton.
- `[tools.py:SymbolNotFoundError]` - Re-exported exception from `ph_stocks_advisor.data.clients.dragonfi` raised when a stock symbol is not listed on the PSE.
- `[tools.py:validate_symbol]` - Invokes the `validate_symbol` MCP tool and returns the normalized symbol string.
- `[tools.py:fetch_stock_price]` - Invokes the `get_stock_price` MCP tool and validates the response into a `StockPrice` model.
- `[tools.py:fetch_dividend_info]` - Invokes the `get_dividend_info` MCP tool and validates the response into a `DividendInfo` model.
- `[tools.py:fetch_price_movement]` - Invokes the `get_price_movement` MCP tool and validates the response into a `PriceMovement` model.
- `[tools.py:fetch_fair_value]` - Invokes the `get_fair_value` MCP tool and validates the response into a `FairValueEstimate` model.
- `[tools.py:fetch_controversy_info]` - Invokes the `get_controversy_info` MCP tool and validates the response into a `ControversyInfo` model.
- `[tools.py:fetch_sentiment_info]` - Invokes the `get_sentiment_info` MCP tool and validates the response into a `SentimentInfo` model.
- `[tools.py:_detect_price_catalysts]` - Re-exported `detect_price_catalysts` helper from `ph_stocks_advisor.data.services.price`.
- Subdirectories:
  - [analysis](./analysis/README.md) - Candlestick pattern detection (`CandlestickSummary`, `analyse_candlesticks`).
  - [clients](./clients/README.md) - External API and scraping clients (DragonFi, PSE EDGE, TradingView, Tavily).
  - [services](./services/README.md) - Domain services orchestrating clients into typed Pydantic models.

## Invariants
- `[models.py:ConsolidationResponse]` - All six dimension sub-scores (`price_score`, `valuation_score`, `dividend_score`, `movement_score`, `controversy_score`, `sentiment_score`) are constrained to `ge=0, le=100` when present.
- `[models.py:FairValueEstimate.is_reit]` - Must reflect authoritative Philippine REIT status (DragonFi `isREIT` or PSE EDGE registry) and never be inferred by the LLM (`[models.py:SentimentInfo.is_reit]` shares this invariant).
- `[models.py:PriceMovement.monthly_prices]` - Retains the field name `monthly_prices` (and `[models.py:FinalReport.movement_monthly_prices]`) for schema/DB compatibility, but stores up to 365 daily closing prices.
- `[mcp_client.py:get_client]` - `MCP_SERVER_URL` (`settings.mcp_server_url`) must be non-empty; there is no in-process fallback, and missing configuration raises `MCPNotConfiguredError`.
- `[mcp_client.py:_SyncMCPClient._ensure_started]` - Process-fork invariant: if `self._owner_pid != os.getpid()` (or in `[mcp_client.py:get_client]`), inherited session/loop state from a parent process is discarded and rebuilt.
- `[mcp_client.py:_SyncMCPClient._call_async]` - Tool error messages containing `_SYMBOL_NOT_FOUND_PREFIX` (`'SymbolNotFoundError: '`) anywhere in the message text are translated and re-raised as `SymbolNotFoundError`.
- `[mcp_client.py:_SyncMCPClient.call]` - Retries failed MCP tool invocations at most once (`for attempt in range(2)`) after resetting the underlying session on non-`SymbolNotFoundError`/`MCPClientError` exceptions.
- `[mcp_client.py:_env_float]` - Environment timeout overrides (`MCP_REQUEST_TIMEOUT`, `MCP_CONNECT_TIMEOUT`) must parse as positive floats (`> 0`) or fall back to `60.0` seconds.

## Side Effects
- `[mcp_client.py:_SyncMCPClient._ensure_started]` - Spawns a native daemon OS thread (`_make_native_thread`, bypassing gevent monkey-patching via `gevent.monkey.get_original`) running a dedicated `asyncio` event loop (`run_forever`) and registers `self.close` with `atexit.register`.
- `[mcp_client.py:_SyncMCPClient._open_session]` - Opens a persistent HTTP streamable MCP connection (`streamablehttp_client` and `ClientSession.initialize()`) against `self._url`.
- `[mcp_client.py:get_client]` - Mutates module-level singleton `_client` under `_client_lock` and reads `MCP_SERVER_URL` from `ph_stocks_advisor.infra.config.get_settings()`.
- `[mcp_client.py:_SyncMCPClient.__init__]` - Reads `MCP_REQUEST_TIMEOUT` and `MCP_CONNECT_TIMEOUT` environment variables via `_env_float`.
- `[tools.py:_call]` - Performs synchronous network RPC calls over MCP for `validate_symbol`, `get_stock_price`, `get_dividend_info`, `get_price_movement`, `get_fair_value`, `get_controversy_info`, and `get_sentiment_info`.

## Verification
- Run: `pytest`
