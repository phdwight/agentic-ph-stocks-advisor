"""
Specialist analysis agents.

Each agent follows the Single Responsibility Principle: it fetches the data
it needs (via the MCP data tools), sends it to the LLM with its specialist
prompt, and returns a typed analysis model.

Dependency Inversion: agents depend on the abstract `BaseChatModel` interface,
not on a concrete OpenAI class.

Agents do NOT bind any non-MCP LangChain tools to the LLM. All external
data flows through the PH Stocks Advisor MCP server (see
``ph_stocks_advisor.data.tools``); the LLM only receives that pre-fetched
data in its prompt.
"""

from __future__ import annotations

import logging

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage

from ph_stocks_advisor.agents.prompts import (
    CONTROVERSY_ANALYSIS_PROMPT,
    DIVIDEND_ANALYSIS_PROMPT,
    MOVEMENT_ANALYSIS_PROMPT,
    PRICE_ANALYSIS_PROMPT,
    SENTIMENT_ANALYSIS_PROMPT,
    VALUATION_ANALYSIS_PROMPT,
)
from ph_stocks_advisor.data.models import (
    ControversyAnalysis,
    DividendAnalysis,
    DividendInfo,
    MovementAnalysis,
    PriceAnalysis,
    PriceMovement,
    SentimentAnalysis,
    StockPrice,
    ValuationAnalysis,
)
from ph_stocks_advisor.data.tools import (
    fetch_controversy_info,
    fetch_dividend_info,
    fetch_fair_value,
    fetch_price_movement,
    fetch_sentiment_info,
    fetch_stock_price,
)
from ph_stocks_advisor.infra.config import get_today

logger = logging.getLogger(__name__)


class EmptyAgentDataError(RuntimeError):
    """Raised when an upstream data tool returns an empty payload.

    "Empty" means the fetched model carries no usable signal — every
    field is either unset, zero, an empty collection, or an empty
    string (the symbol field is ignored). When this happens, the
    downstream LLM analysis would be meaningless, so the workflow
    must abort and surface the error instead of silently producing
    a low-quality report.
    """

    def __init__(self, agent_name: str, symbol: str) -> None:
        self.agent_name = agent_name
        self.symbol = symbol
        super().__init__(
            f"{agent_name} returned an empty data object for symbol '{symbol}'. "
            "Aborting analysis — upstream data source produced no usable signal."
        )


def _is_empty_stock_price(data: StockPrice) -> bool:
    """A `StockPrice` is empty when no meaningful price information exists."""
    return (
        data.current_price <= 0.0
        and data.fifty_two_week_high <= 0.0
        and data.fifty_two_week_low <= 0.0
        and data.previous_close <= 0.0
        and not data.price_catalysts
    )


def _is_empty_dividend_info(data: DividendInfo) -> bool:
    """A `DividendInfo` is empty when every metric and enrichment field is unset."""
    return (
        data.dividend_rate == 0.0
        and data.dividend_yield == 0.0
        and data.payout_ratio == 0.0
        and data.five_year_avg_yield == 0.0
        and data.annual_dividend_per_share == 0.0
        and not data.ex_dividend_date
        and not data.net_income_trend
        and not data.revenue_trend
        and not data.free_cash_flow_trend
        and not data.dividend_sustainability_note
        and not data.recent_dividend_news
        and not data.recent_declared_dividends
        and not data.dividend_announcements
    )


def _is_empty_price_movement(data: PriceMovement) -> bool:
    """A `PriceMovement` is empty when no historical price data is present."""
    return (
        data.year_start_price == 0.0
        and data.year_end_price == 0.0
        and data.year_change_pct == 0.0
        and data.max_price == 0.0
        and data.min_price == 0.0
        and data.volatility == 0.0
        and not data.monthly_prices
        and not data.candlestick_patterns
        and not data.performance_summary
        and not data.web_news
    )


def _invoke_llm(llm: BaseChatModel, prompt: str) -> str:
    """Invoke the LLM with a single human message and return its text."""
    response = llm.invoke([HumanMessage(content=prompt)])
    return str(response.content)


class PriceAgent:
    """Analyses the current stock price relative to its 52-week range."""

    def __init__(self, llm: BaseChatModel) -> None:
        self._llm = llm

    def run(self, symbol: str) -> PriceAnalysis:
        data = fetch_stock_price(symbol)
        if _is_empty_stock_price(data):
            raise EmptyAgentDataError("PriceAgent", symbol)
        prompt = PRICE_ANALYSIS_PROMPT.format(
            symbol=symbol,
            data=data.model_dump_json(indent=2),
            today=get_today().isoformat(),
        )
        return PriceAnalysis(data=data, analysis=_invoke_llm(self._llm, prompt))


class DividendAgent:
    """Analyses dividend yield and sustainability."""

    def __init__(self, llm: BaseChatModel) -> None:
        self._llm = llm

    def run(self, symbol: str) -> DividendAnalysis:
        data = fetch_dividend_info(symbol)
        if _is_empty_dividend_info(data):
            raise EmptyAgentDataError("DividendAgent", symbol)
        prompt = DIVIDEND_ANALYSIS_PROMPT.format(
            symbol=symbol,
            data=data.model_dump_json(indent=2),
            today=get_today().isoformat(),
        )
        return DividendAnalysis(data=data, analysis=_invoke_llm(self._llm, prompt))


class MovementAgent:
    """Analyses 1-year price trend, volatility, and patterns."""

    def __init__(self, llm: BaseChatModel) -> None:
        self._llm = llm

    def run(self, symbol: str) -> MovementAnalysis:
        data = fetch_price_movement(symbol)
        if _is_empty_price_movement(data):
            raise EmptyAgentDataError("MovementAgent", symbol)
        prompt = MOVEMENT_ANALYSIS_PROMPT.format(
            symbol=symbol,
            data=data.model_dump_json(indent=2),
            today=get_today().isoformat(),
        )
        return MovementAnalysis(data=data, analysis=_invoke_llm(self._llm, prompt))


class ValuationAgent:
    """Analyses fair value, PE/PB ratios, and discount/premium."""

    def __init__(self, llm: BaseChatModel) -> None:
        self._llm = llm

    def run(self, symbol: str) -> ValuationAnalysis:
        data = fetch_fair_value(symbol)
        prompt = VALUATION_ANALYSIS_PROMPT.format(
            symbol=symbol,
            data=data.model_dump_json(indent=2),
            today=get_today().isoformat(),
        )
        return ValuationAnalysis(data=data, analysis=_invoke_llm(self._llm, prompt))


class ControversyAgent:
    """Detects price anomalies and flags risk factors."""

    def __init__(self, llm: BaseChatModel) -> None:
        self._llm = llm

    def run(self, symbol: str) -> ControversyAnalysis:
        data = fetch_controversy_info(symbol)
        prompt = CONTROVERSY_ANALYSIS_PROMPT.format(
            symbol=symbol,
            data=data.model_dump_json(indent=2),
            today=get_today().isoformat(),
        )
        return ControversyAnalysis(data=data, analysis=_invoke_llm(self._llm, prompt))


class SentimentAgent:
    """Analyses global events and macro-level sentiment.

    Evaluates geopolitical risks, pandemics, global economic shifts,
    and climate events that may impact the Philippine market and the
    specific stock under analysis.
    """

    def __init__(self, llm: BaseChatModel) -> None:
        self._llm = llm

    def run(self, symbol: str) -> SentimentAnalysis:
        data = fetch_sentiment_info(symbol)
        prompt = SENTIMENT_ANALYSIS_PROMPT.format(
            symbol=symbol,
            data=data.model_dump_json(indent=2),
            today=get_today().isoformat(),
        )
        return SentimentAnalysis(data=data, analysis=_invoke_llm(self._llm, prompt))
