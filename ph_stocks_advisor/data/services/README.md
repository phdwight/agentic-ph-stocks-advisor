# services

## Overview
Domain service layer that orchestrates external data clients (DragonFi, PSE EDGE, TradingView, Tavily) and analytical helpers into typed Pydantic domain models (`StockPrice`, `DividendInfo`, `PriceMovement`, `FairValueEstimate`, `ControversyInfo`, `SentimentInfo`) with deterministic calculations and multi-source fallback paths.

## Interface
- `[controversy.py:fetch_controversy_info]` - `(symbol: str) -> ControversyInfo`: Normalizes symbol, detects historical daily price spikes and volatility/valuation risk factors from 1-year PSE EDGE OHLCV data, fetches up to 5 recent news headlines from DragonFi, and returns a `ControversyInfo` model.
- `[dividend.py:fetch_dividend_info]` - `(symbol: str) -> DividendInfo`: Normalizes symbol, retrieves dividend yield, REIT status, and multi-year income/cash-flow trends from DragonFi alongside SEC Form 6-1 declarations and company dividend announcements from PSE EDGE, computing per-share dividend rate, net-income payout ratio, FCF payout ratio, and a sustainability note.
- `[movement.py:fetch_price_movement]` - `(symbol: str) -> PriceMovement`: Normalizes symbol and computes 1-year price movement metrics (start/end/min/max prices, % change, volatility, max drawdown, trend direction, up to 365 daily closes, candlestick patterns, price catalysts, and TradingView performance summary) using PSE EDGE OHLCV as primary source and DragonFi 52-week range + TradingView snapshot as fallback.
- `[price.py:detect_price_catalysts]` - `(profile: dict[str, Any] | None) -> list[str]`: Infers human-readable price catalyst descriptions from a stock profile dictionary by comparing dividend yield, REIT status, daily price change, and 52-week range position against configured settings thresholds.
- `[price.py:fetch_stock_price]` - `(symbol: str) -> StockPrice`: Normalizes symbol and fetches a current PHP price snapshot (current price, 52-week high/low, previous close, and detected price catalysts) from DragonFi with fallback to the PSE EDGE `stockData` snapshot.
- `[sentiment.py:fetch_sentiment_info]` - `(symbol: str) -> SentimentInfo`: Normalizes symbol and gathers macro/sentiment context including sector, deterministic REIT flag (from DragonFi profile or PSE EDGE registry fallback), global events news via Tavily, and BSP policy-rate context via Tavily.
- `[valuation.py:fetch_fair_value]` - `(symbol: str) -> FairValueEstimate`: Normalizes symbol and computes fundamental valuation metrics (P/E, P/B, book value, EPS, Graham Number fair value or 15x EPS fallback, discount percentage, and REIT status) from DragonFi profile/valuation endpoints with fallback to PSE EDGE snapshot and annual financials.

## Invariants
- `[controversy.py:fetch_controversy_info]` - Input `symbol` is normalized via `symbol.upper().replace('.PS', '')` before any client calls or model construction (also enforced in `[dividend.py:fetch_dividend_info]`, `[movement.py:fetch_price_movement]`, `[price.py:fetch_stock_price]`, `[sentiment.py:fetch_sentiment_info]`, and `[valuation.py:fetch_fair_value]`).
- `[controversy.py:_fetch_history]` - Catches all exceptions from `fetch_pse_edge_ohlcv(symbol, days=365)` and always returns a `pd.DataFrame` (empty on failure), never raising.
- `[controversy.py:fetch_controversy_info]` - Daily return is flagged as a sudden spike only if both `abs(ret) > s.spike_std_multiplier * std_ret` and `abs(ret) > s.spike_min_abs_return` hold.
- `[dividend.py:fetch_dividend_info]` - DragonFi `dividendYield` (`div_yield_raw`) is normalized to a decimal via `div_yield_raw / 100.0 if div_yield_raw > 1 else div_yield_raw`.
- `[dividend.py:fetch_dividend_info]` - `payout_ratio` and `fcf_payout_ratio` are computed deterministically in code (`round(total_dividends / latest_ni, 4)` and `round((dividend_rate * shares_outstanding) / latest_fcf, 4)`) only when `dividend_rate > 0`, `shares_outstanding > 0`, and latest net income / FCF > 0; otherwise they default to `0.0`.
- `[dividend.py:fetch_dividend_info]` - `is_reit` is resolved deterministically from registry/profile data (`bool(profile.get('isREIT', False))` or `is_reit_from_edge(symbol) is True` during a DragonFi profile outage, mapping unknown `None` to `False`) and preserved even when dividend yield is zero or unavailable (same deterministic REIT contract enforced in `[sentiment.py:_fetch_profile_context]` and `[valuation.py:fetch_fair_value]`).
- `[dividend.py:fetch_dividend_info]` - The PSE EDGE outage enrichment branch (`if not profile:`) only executes when DragonFi returns no profile at all; a live profile with `div_yield_raw <= 0` skips enrichment and returns a minimal `DividendInfo(symbol=symbol, is_reit=is_reit)`.
- `[movement.py:_classify_trend]` - Classifies `change_pct` strictly as `TrendDirection.UPTREND` when `> s.trend_up_threshold`, `TrendDirection.DOWNTREND` when `< s.trend_down_threshold`, and `TrendDirection.SIDEWAYS` otherwise.
- `[movement.py:fetch_price_movement]` - `monthly_prices` in the primary OHLCV branch contains at most the last 365 daily closing prices (`[-365:]`), rounded to 2 decimal places.
- `[price.py:fetch_stock_price]` - `StockPrice.currency` is always hardcoded to `'PHP'`, and complete failure across DragonFi and PSE EDGE returns `StockPrice(symbol=symbol, current_price=0.0, currency='PHP')`.
- `[valuation.py:_graham_number]` - Computes `round(math.sqrt(22.5 * eps * book_value), 2)` only when both `eps > 0` and `book_value > 0`; otherwise returns `0.0`.
- `[valuation.py:fetch_fair_value]` - When `_graham_number` returns `0.0` (e.g., missing/non-positive book value) but `eps > 0` (or `pe > 0 and current_price > 0`), `estimated_fair_value` falls back to `15 * EPS` (`round((current_price / pe) * 15, 2)` or `round(eps * 15, 2)`).
- `[valuation.py:_discount_pct]` - Computes `round(((fair_value - current_price) / fair_value) * 100, 2)` when `fair_value > 0` (positive = undervalued, negative = overvalued), else `0.0`.

## Side Effects
- `[controversy.py:fetch_controversy_info]` - Performs network I/O via `fetch_pse_edge_ohlcv` (PSE EDGE), `fetch_stock_news` (DragonFi), reads global settings via `get_settings()`, and logs debug messages on OHLCV failure via `logger.debug`.
- `[dividend.py:fetch_dividend_info]` - Performs network I/O via DragonFi (`fetch_stock_profile`, `fetch_annual_income_trends`, `fetch_annual_cashflow_trends`) and PSE EDGE (`fetch_recent_dividend_declarations`, `fetch_company_dividend_announcements`, `fetch_annual_financials`, `is_reit_from_edge`), and emits warnings via `logger.warning` on client failures or missing dividend data.
- `[movement.py:fetch_price_movement]` - Performs network I/O via `fetch_pse_edge_ohlcv` (PSE EDGE), `fetch_stock_profile` (DragonFi), and `fetch_tradingview_snapshot` (TradingView), reads settings via `get_settings()`, and logs fallback transitions via `logger.info`.
- `[price.py:fetch_stock_price]` - Performs network I/O via `fetch_stock_profile` (DragonFi) and fallback `fetch_stock_snapshot` (PSE EDGE), reads settings via `get_settings()` in `detect_price_catalysts`, and logs fallback/failure warnings via `logger.warning`.
- `[sentiment.py:fetch_sentiment_info]` - Performs network I/O via DragonFi (`fetch_stock_profile`), PSE EDGE (`is_reit_from_edge`), and Tavily (`search_global_events`, `search_bsp_rate`), logging search failures via `logger.debug`.
- `[valuation.py:fetch_fair_value]` - Performs network I/O via DragonFi (`fetch_stock_profile`, `fetch_security_valuation`) and fallback PSE EDGE (`fetch_stock_snapshot`, `fetch_annual_financials`, `is_reit_from_edge`), and logs warnings via `logger.warning` when falling back or when valuation data is unavailable.

## Verification
- Run: `pytest`
