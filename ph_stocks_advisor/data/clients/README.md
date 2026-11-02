# clients

## Overview
Provides third-party API and web-scraping clients for fetching Philippine Stock Exchange (PSE) market data, fundamentals, dividend disclosures, technical performance metrics, and macro news from DragonFi, PSE EDGE, TradingView, and Tavily, with symbol validation, multi-source fallback resolution, and process-lifetime memoization of stable registry mappings.

## Interface
- `[dragonfi.py:SymbolValidationUnavailableError]` - Exception raised when PSE listing status cannot be verified due to transient upstream failure.
- `[dragonfi.py:SymbolNotFoundError]` - Exception raised when a ticker is definitively not listed on the PSE.
- `[dragonfi.py:validate_pse_symbol]` - Validates that a symbol is a listed PSE stock via DragonFi (with PSE EDGE fallback) and returns the canonical uppercase code.
- `[dragonfi.py:fetch_stock_profile]` - Fetches the full stock profile dict from DragonFi's `Securities/GetStockProfile` endpoint.
- `[dragonfi.py:fetch_security_valuation]` - Fetches annual valuation multiples dict from DragonFi's `Securities/GetSecurityValuation` endpoint.
- `[dragonfi.py:fetch_security_metrics]` - Fetches financial metrics dict from DragonFi's `Securities/GetSecurityMetrics` endpoint.
- `[dragonfi.py:fetch_stock_financials]` - Fetches income, balance-sheet, and cash-flow statements dict from DragonFi's `Securities/GetStockFinancialStatements` endpoint.
- `[dragonfi.py:fetch_stock_news]` - Fetches up to `page_size` recent news articles for a symbol from DragonFi's `News/GetNews` endpoint.
- `[dragonfi.py:fetch_annual_income_trends]` - Extracts multi-year `revenue`, `net_income`, and `operation_income` trend dicts from DragonFi annual income statements.
- `[dragonfi.py:fetch_annual_cashflow_trends]` - Extracts multi-year `cfo`, `cfi`, `cff`, and `fcf` trend dicts from DragonFi annual cash-flow statements and security metrics.
- `[pse_edge.py:symbol_exists]` - Tri-state check (`True`/`False`/`None`) verifying whether an exact symbol match exists in PSE EDGE's autocomplete endpoint.
- `[pse_edge.py:is_reit_from_edge]` - Tri-state check (`True`/`False`/`None`) determining REIT status from PSE EDGE company name and Company Description text.
- `[pse_edge.py:fetch_stock_snapshot]` - Scrapes a market-data snapshot (`price`, `previous_close`, `week_high`, `week_low`, `shares_outstanding`) from the PSE EDGE `stockData` page.
- `[pse_edge.py:fetch_annual_financials]` - Scrapes audited annual fundamentals (`fiscal_year`, `revenue`, `net_income`, `eps`, `eps_previous`, `book_value_per_share`, `bvps_previous`) from the PSE EDGE financial reports page.
- `[pse_edge.py:fetch_pse_edge_ohlcv]` - Fetches daily OHLCV DataFrame (with PHP value as `Volume`) from PSE EDGE's `/common/DisclosureCht.ax` endpoint.
- `[pse_edge_company_dividends.py:fetch_company_dividend_announcements]` - Scrapes up to `max_results` `DividendAnnouncement` objects from a company's PSE EDGE `dividends_and_rights_list.ax` table.
- `[pse_edge_dividends.py:DeclaredDividend]` - Dataclass representing a single cash-dividend declaration scraped from PSE EDGE SEC Form 6-1 disclosures, with a `to_summary()` helper.
- `[pse_edge_dividends.py:fetch_recent_dividend_declarations]` - Scans recent PSE-wide `'Declaration of Cash Dividends'` disclosures on PSE EDGE and returns up to `max_matches` `DeclaredDividend` items for a symbol.
- `[tavily_search.py:search_bsp_rate]` - Queries Tavily for current BSP policy interest rate and PH 10-year bond yield context, returning a formatted string.
- `[tavily_search.py:search_global_events]` - Queries Tavily for global geopolitical, economic, and climate events affecting the Philippine stock market, returning a formatted string.
- `[tradingview.py:fetch_tradingview_snapshot]` - Fetches OHLCV, multi-period performance, volatility, and 52-week high/low metrics for a PSE stock from TradingView's public scanner API.
- `[tradingview.py:format_tv_performance_summary]` - Formats a TradingView snapshot dict into a human/LLM-readable performance and monthly volatility summary string.

## Invariants
- `[dragonfi.py:_fetch_all_stock_codes]` - Only non-empty stock code sets are cached in `_STOCK_CODES_CACHE`; empty responses from DragonFi outages are never cached.
- `[dragonfi.py:validate_pse_symbol]` - Strips `'.PS'` and uppercases symbols; raises `SymbolNotFoundError` only when DragonFi's full stock list is populated and all probes (DragonFi list, DragonFi profile, PSE EDGE) miss, otherwise raises `SymbolValidationUnavailableError` when upstream sources are unreachable.
- `[dragonfi.py:_extract_annual_values]` - Filters out non-digit keys, keys containing `'_'`, and `None` values so YoY change keys (`'2020_YoY'`) and metadata keys are excluded from annual series.
- `[pse_edge.py:symbol_exists]` - Returns `None` on HTTP/network errors rather than `False` so callers never confuse an unreachable endpoint with `'not listed'`.
- `[pse_edge.py:is_reit_from_edge]` - Determines REIT status strictly from deterministic registry signals (company name containing `'REIT'` or Company Description matching `'real estate investment trust'`) and returns `None` on upstream failure; caches only definitive boolean results in `_IS_REIT_CACHE`.
- `[pse_edge.py:fetch_stock_snapshot]` - Returns `None` if scraped `price <= 0.0`, and deliberately omits P/E and Book Value fields because PSE EDGE frequently leaves them blank.
- `[pse_edge.py:fetch_annual_financials]` - Ignores quarterly tables (requires `'>Current Year<'` header) and restricts inferred `fiscal_year` dates to `[this_year - 3, this_year + 1]`.
- `[pse_edge.py:fetch_pse_edge_ohlcv]` - Deduplicates `chartData` records by `CHART_DATE` string and sorts the resulting DataFrame by its `DatetimeIndex`.
- `[pse_edge_company_dividends.py:_parse_dividend_rows]` - Skips HTML table rows with fewer than 6 `<td>` cells or missing `dividend_rate` or `ex_date`.
- `[tavily_search.py:_get_client]` - Gracefully degrades by returning `None` when `TAVILY_API_KEY` is unset or client initialization fails, causing all search functions to return fallback strings without raising exceptions.
- `[tradingview.py:fetch_tradingview_snapshot]` - Prefixes normalized PSE ticker with `'PSE:'` and coerces `None` metric values in the scanner response to `0.0`.

## Side Effects
- `[dragonfi.py:_get]` - Performs outbound HTTP GET requests to DragonFi API endpoints configured via `get_settings().dragonfi_base_url`.
- `[dragonfi.py:_STOCK_CODES_CACHE]` - Mutates module-level global cache `_STOCK_CODES_CACHE` on first non-empty stock code list fetch.
- `[pse_edge.py:_CMPY_ID_CACHE]` - Mutates module-level dictionary caching `symbol`-to-`cmpy_id` mappings across calls.
- `[pse_edge.py:_ID_CACHE]` - Mutates module-level dictionary caching `symbol`-to-`(cmpy_id, security_id)` tuples across calls.
- `[pse_edge.py:_IS_REIT_CACHE]` - Mutates module-level dictionary caching `symbol`-to-`bool` REIT determinations across calls.
- `[pse_edge.py:fetch_pse_edge_ohlcv]` - Performs outbound HTTP GET and POST requests to PSE EDGE endpoints configured via `get_settings().pse_edge_base_url`.
- `[pse_edge_company_dividends.py:_CMPY_ID_CACHE]` - Mutates module-level dictionary caching `symbol`-to-`cmpy_id` mappings and performs outbound HTTP GET/POST requests to PSE EDGE.
- `[pse_edge_dividends.py:fetch_recent_dividend_declarations]` - Performs outbound HTTP POST and GET requests to PSE EDGE disclosure search, viewer, and HTML download endpoints.
- `[tavily_search.py:_search]` - Performs outbound HTTP requests to the Tavily API via `TavilyClient.search` when `TAVILY_API_KEY` is configured.
- `[tradingview.py:fetch_tradingview_snapshot]` - Performs outbound HTTP POST requests to TradingView's scanner URL configured via `get_settings().tradingview_scanner_url`.

## Verification
- Run: `pytest`
