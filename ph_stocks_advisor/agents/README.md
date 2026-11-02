# agents

## Overview
Implements the LLM-driven analysis layer of the PH Stocks Advisor, providing six domain-specific `SpecialistAgent` subclasses that fetch MCP market data and narrate it, a `ConsolidatorAgent` that synthesizes specialist narratives and weighted sub-scores into a `FinalReport`, a `PortfolioAgent` for personalized holding advisories, and the prompt templates governing all agent instructions.

## Interface
- `[consolidator.py:ConsolidatorAgent]` - Agent initialized with a `BaseChatModel` whose `run(state: AdvisorState) -> FinalReport` method synthesizes six specialist analyses into a unified `FinalReport` with a derived `Verdict`, summary, and 0–100 score.
- `[portfolio.py:PortfolioAgent]` - Agent initialized with a `BaseChatModel` whose `run(*, symbol, shares, avg_cost, current_price, base_report, sentiment_context, user_id, session_id) -> str` method generates a markdown-formatted personalized hold/accumulate/trim advisory note.
- `[prompts.py:PRICE_ANALYSIS_PROMPT]` - Prompt template for 52-week range, momentum, and price catalyst analysis.
- `[prompts.py:DIVIDEND_ANALYSIS_PROMPT]` - Prompt template for dividend yield, income/FCF sustainability, Philippine REIT payout rules (RA 9856), and PSE EDGE dividend declarations.
- `[prompts.py:MOVEMENT_ANALYSIS_PROMPT]` - Prompt template for 1-year price trend, max drawdown (>10% threshold), candlestick patterns, and multi-period performance.
- `[prompts.py:SENTIMENT_ANALYSIS_PROMPT]` - Prompt template for geopolitical, pandemic, macro-economic, climate, and BSP interest-rate/REIT bond-proxy sentiment analysis.
- `[prompts.py:VALUATION_ANALYSIS_PROMPT]` - Prompt template for fair value, P/E and P/B comparisons, margin of safety, and 3-model Philippine REIT valuation (40% NAV, 40% DDM, 20% Yield-Spread plus +/-10% moat adjustment).
- `[prompts.py:CONTROVERSY_ANALYSIS_PROMPT]` - Prompt template for sudden price spike anomalies, risk factors, and news/controversy analysis.
- `[prompts.py:CONSOLIDATION_PROMPT]` - Prompt template for synthesizing all six specialist analyses, formatting the executive summary metrics table, handling missing data (`'DATA UNAVAILABLE'`), and rubric-scoring each dimension from 0 to 100.
- `[prompts.py:PORTFOLIO_ANALYSIS_PROMPT]` - Prompt template for generating a personalized portfolio advisory note with position P/L assessment, HOLD/ACCUMULATE/TRIM recommendation, and action price levels table.
- `[specialists.py:EmptyAgentDataError]` - `RuntimeError` subclass raised with `agent_name` and `symbol` attributes when an upstream data tool returns an empty payload with no usable signal.
- `[specialists.py:SpecialistAgent]` - Abstract base class separating data retrieval (`fetch(symbol)`) from LLM narration (`analyze(symbol, data)` and `wrap(data, analysis)`), with `run(symbol)` as a single-shot entry point.
- `[specialists.py:PriceAgent]` - `SpecialistAgent` subclass that fetches `StockPrice` via `fetch_stock_price`, checks emptiness via `_is_empty_stock_price`, and produces `PriceAnalysis`.
- `[specialists.py:DividendAgent]` - `SpecialistAgent` subclass that fetches `DividendInfo` via `fetch_dividend_info`, checks emptiness via `_is_empty_dividend_info`, and produces `DividendAnalysis`.
- `[specialists.py:MovementAgent]` - `SpecialistAgent` subclass that fetches `PriceMovement` via `fetch_price_movement`, checks emptiness via `_is_empty_price_movement`, and produces `MovementAnalysis`.
- `[specialists.py:ValuationAgent]` - `SpecialistAgent` subclass that fetches valuation data via `fetch_fair_value` and produces `ValuationAnalysis`.
- `[specialists.py:ControversyAgent]` - `SpecialistAgent` subclass that fetches controversy/anomaly data via `fetch_controversy_info` and produces `ControversyAnalysis`.
- `[specialists.py:SentimentAgent]` - `SpecialistAgent` subclass that fetches macro/global-events data via `fetch_sentiment_info` and produces `SentimentAnalysis`.

## Invariants
- `[consolidator.py:_GAP_SCORE_FIELDS]` - Any specialist dimension listed in `state.data_gaps` has its corresponding sub-score field on `ConsolidationResponse` forced to `None` before computing the weighted score, deterministically excluding missing-data dimensions even if the LLM emitted a score.
- `[consolidator.py:ConsolidatorAgent._weighted_score]` - The final score is the normalized weighted average of present sub-scores (with positive weights from `get_settings()`), rounded and clamped to `[0, 100]` (`max(0, min(100, round(raw)))`); returns `None` if no valid weighted sub-scores are present.
- `[consolidator.py:ConsolidatorAgent._invoke_structured]` - When `_weighted_score` returns a non-null integer, the binary `Verdict` is deterministically derived (`Verdict.BUY` if `score >= get_settings().buy_score_threshold` else `Verdict.NOT_BUY`), overriding `result.verdict` from the LLM so the badge and score band never contradict.
- `[consolidator.py:_FALLBACK_BUY_SCORE]` - When structured output is unsupported (`NotImplementedError`, `AttributeError`, `TypeError`) or all sub-scores are omitted (`score is None`), fallback scores of `75` (`_FALLBACK_BUY_SCORE` for `Verdict.BUY`) or `25` (`_FALLBACK_NOT_BUY_SCORE` for `Verdict.NOT_BUY`) are assigned.
- `[consolidator.py:ConsolidatorAgent._extract_verdict]` - Unstructured text verdict parsing checks explicit `Verdict: [NOT] BUY` headers first, then the last word-boundary occurrence of `NOT BUY` vs `BUY`, and conservatively defaults to `Verdict.NOT_BUY` if no pattern matches.
- `[specialists.py:SpecialistAgent.fetch]` - Calls `_fetch(symbol)` and raises `EmptyAgentDataError(self.agent_name, symbol)` if `self._is_empty(data)` returns `True`; `ValuationAgent`, `ControversyAgent`, and `SentimentAgent` inherit the base `_is_empty` which always returns `False`.
- `[specialists.py:_is_empty_stock_price]` - `StockPrice` is considered empty iff `current_price <= 0.0`, `fifty_two_week_high <= 0.0`, `fifty_two_week_low <= 0.0`, `previous_close <= 0.0`, and `price_catalysts` is empty.
- `[specialists.py:_is_empty_dividend_info]` - `DividendInfo` is considered empty iff all numeric dividend metrics equal `0.0` (`dividend_rate`, `dividend_yield`, `payout_ratio`, `five_year_avg_yield`, `annual_dividend_per_share`) and all date/trend/news/announcement fields (`ex_dividend_date`, `net_income_trend`, `revenue_trend`, `free_cash_flow_trend`, `dividend_sustainability_note`, `recent_dividend_news`, `recent_declared_dividends`, `dividend_announcements`) are empty.
- `[specialists.py:_is_empty_price_movement]` - `PriceMovement` is considered empty iff all price/change/volatility fields equal `0.0` (`year_start_price`, `year_end_price`, `year_change_pct`, `max_price`, `min_price`, `volatility`) and `monthly_prices`, `candlestick_patterns`, `performance_summary`, and `web_news` are all empty.
- `[portfolio.py:PortfolioAgent.run]` - Computes `unrealised_pl_pct` as `(unrealised_pl / total_cost * 100)` when `total_cost` is non-zero, guarding against division by zero by defaulting to `0.0`.

## Side Effects
- `[consolidator.py:ConsolidatorAgent._invoke_structured]` - Invokes the external LLM via `build_structured_llm(self._llm, ConsolidationResponse).invoke(...)` (or `self._llm.invoke(...)` on fallback), reads global configuration via `get_settings()`, and emits `logger.info` records.
- `[consolidator.py:ConsolidatorAgent.run]` - Calls `get_today()` to inject the current date into `CONSOLIDATION_PROMPT`.
- `[portfolio.py:PortfolioAgent.run]` - Calls `get_today()`, builds Langfuse tracing callbacks/metadata via `build_langfuse_config`, invokes `self._llm.invoke(..., config=invoke_config)`, and unconditionally flushes Langfuse telemetry via `flush_langfuse()` in a `finally` block.
- `[specialists.py:SpecialistAgent.fetch]` - Invokes external/MCP data retrieval tools (`fetch_stock_price`, `fetch_dividend_info`, `fetch_price_movement`, `fetch_fair_value`, `fetch_controversy_info`, `fetch_sentiment_info`) in subclass `_fetch` implementations.
- `[specialists.py:SpecialistAgent.analyze]` - Calls `get_today()` and invokes the LLM via `_invoke_llm(self._llm, prompt)` (`llm.invoke([HumanMessage(content=prompt)])`).

## Verification
- Run: `pytest`
