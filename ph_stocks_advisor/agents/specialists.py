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
    MovementAnalysis,
    PriceAnalysis,
    SentimentAnalysis,
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
