"""
Specialist analysis agents.

Each agent follows the Single Responsibility Principle: it fetches the data
it needs, sends it to the LLM with its specialist prompt, and returns a
typed analysis model.

Dependency Inversion: agents depend on the abstract `BaseChatModel` interface,
not on a concrete OpenAI class.
"""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage

from ph_stocks_advisor.data.models import (
    ControversyAnalysis,
    DividendAnalysis,
    MovementAnalysis,
    PriceAnalysis,
    ValuationAnalysis,
)
from ph_stocks_advisor.infra.config import get_today
from ph_stocks_advisor.agents.prompts import (
    CONTROVERSY_ANALYSIS_PROMPT,
    DIVIDEND_ANALYSIS_PROMPT,
    MOVEMENT_ANALYSIS_PROMPT,
    PRICE_ANALYSIS_PROMPT,
    VALUATION_ANALYSIS_PROMPT,
)
from ph_stocks_advisor.data.tools import (
    fetch_controversy_info,
    fetch_dividend_info,
    fetch_fair_value,
    fetch_price_movement,
    fetch_stock_price,
)


class PriceAgent:
    """Analyses the current stock price relative to its 52-week range."""

    def __init__(self, llm: BaseChatModel) -> None:
        self._llm = llm

    def run(self, symbol: str) -> PriceAnalysis:
        data = fetch_stock_price(symbol)
        prompt = PRICE_ANALYSIS_PROMPT.format(
            symbol=symbol, data=data.model_dump_json(indent=2),
            today=get_today().isoformat(),
        )
        response = self._llm.invoke([HumanMessage(content=prompt)])
        return PriceAnalysis(data=data, analysis=str(response.content))


class DividendAgent:
    """Analyses dividend yield and sustainability."""

    def __init__(self, llm: BaseChatModel) -> None:
        self._llm = llm

    def run(self, symbol: str) -> DividendAnalysis:
        data = fetch_dividend_info(symbol)
        prompt = DIVIDEND_ANALYSIS_PROMPT.format(
            symbol=symbol, data=data.model_dump_json(indent=2),
            today=get_today().isoformat(),
        )
        response = self._llm.invoke([HumanMessage(content=prompt)])
        return DividendAnalysis(data=data, analysis=str(response.content))


class MovementAgent:
    """Analyses 1-year price trend, volatility, and patterns."""

    def __init__(self, llm: BaseChatModel) -> None:
        self._llm = llm

    def run(self, symbol: str) -> MovementAnalysis:
        data = fetch_price_movement(symbol)
        prompt = MOVEMENT_ANALYSIS_PROMPT.format(
            symbol=symbol, data=data.model_dump_json(indent=2),
            today=get_today().isoformat(),
        )
        response = self._llm.invoke([HumanMessage(content=prompt)])
        return MovementAnalysis(data=data, analysis=str(response.content))


class ValuationAgent:
    """Analyses fair value, PE/PB ratios, and discount/premium."""

    def __init__(self, llm: BaseChatModel) -> None:
        self._llm = llm

    def run(self, symbol: str) -> ValuationAnalysis:
        data = fetch_fair_value(symbol)
        prompt = VALUATION_ANALYSIS_PROMPT.format(
            symbol=symbol, data=data.model_dump_json(indent=2),
            today=get_today().isoformat(),
        )
        response = self._llm.invoke([HumanMessage(content=prompt)])
        return ValuationAnalysis(data=data, analysis=str(response.content))


class ControversyAgent:
    """Detects price anomalies and flags risk factors."""

    def __init__(self, llm: BaseChatModel) -> None:
        self._llm = llm

    def run(self, symbol: str) -> ControversyAnalysis:
        data = fetch_controversy_info(symbol)
        prompt = CONTROVERSY_ANALYSIS_PROMPT.format(
            symbol=symbol, data=data.model_dump_json(indent=2),
            today=get_today().isoformat(),
        )
        response = self._llm.invoke([HumanMessage(content=prompt)])
        return ControversyAnalysis(data=data, analysis=str(response.content))
