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


class SpecialistAgent:
    """Base specialist agent: fetch market data, then narrate it with the LLM.

    ``fetch`` (pure data retrieval) and ``analyze`` (LLM narration) are kept
    deliberately separate so a caller can keep the fetched data — e.g. the
    movement 1-year trend-line series — even when LLM narration fails. Data
    driven visualisations therefore never depend on the LLM succeeding
    (Single Responsibility: retrieval vs. narration).

    Subclasses declare ``agent_name``, ``result_model`` and
    ``prompt_template`` and implement ``_fetch``; ``_is_empty`` is optional
    (defaults to "never empty" for dimensions that always carry signal).
    New specialists extend this base rather than modifying the workflow
    (Open/Closed).
    """

    agent_name: str
    result_model: type
    prompt_template: str

    def __init__(self, llm: BaseChatModel) -> None:
        self._llm = llm

    def _fetch(self, symbol: str):
        """Retrieve the dimension's raw data model (subclass hook)."""
        raise NotImplementedError

    def _is_empty(self, data) -> bool:
        """Whether *data* carries no usable signal (subclass hook)."""
        return False

    def fetch(self, symbol: str):
        """Retrieve the dimension's data, raising ``EmptyAgentDataError`` on no signal."""
        data = self._fetch(symbol)
        if self._is_empty(data):
            raise EmptyAgentDataError(self.agent_name, symbol)
        return data

    def wrap(self, data, analysis: str):
        """Combine fetched *data* with an *analysis* narrative into the result model."""
        return self.result_model(data=data, analysis=analysis)

    def analyze(self, symbol: str, data):
        """Produce the LLM narrative for already-fetched *data* and wrap both."""
        prompt = self.prompt_template.format(
            symbol=symbol,
            data=data.model_dump_json(indent=2),
            today=get_today().isoformat(),
        )
        return self.wrap(data, _invoke_llm(self._llm, prompt))

    def run(self, symbol: str):
        """Fetch then analyze — the single-shot entry point (back-compat)."""
        return self.analyze(symbol, self.fetch(symbol))


class PriceAgent(SpecialistAgent):
    """Analyses the current stock price relative to its 52-week range."""

    agent_name = "PriceAgent"
    result_model = PriceAnalysis
    prompt_template = PRICE_ANALYSIS_PROMPT

    def _fetch(self, symbol: str) -> StockPrice:
        return fetch_stock_price(symbol)

    def _is_empty(self, data) -> bool:
        return _is_empty_stock_price(data)


class DividendAgent(SpecialistAgent):
    """Analyses dividend yield and sustainability."""

    agent_name = "DividendAgent"
    result_model = DividendAnalysis
    prompt_template = DIVIDEND_ANALYSIS_PROMPT

    def _fetch(self, symbol: str) -> DividendInfo:
        return fetch_dividend_info(symbol)

    def _is_empty(self, data) -> bool:
        return _is_empty_dividend_info(data)


class MovementAgent(SpecialistAgent):
    """Analyses 1-year price trend, volatility, and patterns."""

    agent_name = "MovementAgent"
    result_model = MovementAnalysis
    prompt_template = MOVEMENT_ANALYSIS_PROMPT

    def _fetch(self, symbol: str) -> PriceMovement:
        return fetch_price_movement(symbol)

    def _is_empty(self, data) -> bool:
        return _is_empty_price_movement(data)


class ValuationAgent(SpecialistAgent):
    """Analyses fair value, PE/PB ratios, and discount/premium."""

    agent_name = "ValuationAgent"
    result_model = ValuationAnalysis
    prompt_template = VALUATION_ANALYSIS_PROMPT

    def _fetch(self, symbol: str):
        return fetch_fair_value(symbol)


class ControversyAgent(SpecialistAgent):
    """Detects price anomalies and flags risk factors."""

    agent_name = "ControversyAgent"
    result_model = ControversyAnalysis
    prompt_template = CONTROVERSY_ANALYSIS_PROMPT

    def _fetch(self, symbol: str):
        return fetch_controversy_info(symbol)


class SentimentAgent(SpecialistAgent):
    """Analyses global events and macro-level sentiment.

    Evaluates geopolitical risks, pandemics, global economic shifts,
    and climate events that may impact the Philippine market and the
    specific stock under analysis.
    """

    agent_name = "SentimentAgent"
    result_model = SentimentAnalysis
    prompt_template = SENTIMENT_ANALYSIS_PROMPT

    def _fetch(self, symbol: str):
        return fetch_sentiment_info(symbol)
