"""
Specialist analysis agents.

Each agent follows the Single Responsibility Principle: it fetches the data
it needs, sends it to the LLM with its specialist prompt, and returns a
typed analysis model.

Dependency Inversion: agents depend on the abstract `BaseChatModel` interface,
not on a concrete OpenAI class.

The Dividend, Movement, and Controversy agents have LangChain tools bound
so the LLM can autonomously decide whether to invoke a web search (Tavily)
for additional context.
"""

from __future__ import annotations

import logging

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import BaseTool

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

logger = logging.getLogger(__name__)

# Maximum number of tool-calling rounds before returning a final answer.
_MAX_TOOL_ROUNDS = 2


def _run_with_tools(
    llm: BaseChatModel,
    prompt: str,
    tools: list[BaseTool],
    max_rounds: int = _MAX_TOOL_ROUNDS,
) -> str:
    """Invoke the LLM with optional tool calling.

    If the LLM supports tool calling, the tools are bound and the LLM
    can decide to call them zero or more times.  After *max_rounds*
    tool-calling iterations (or when the LLM stops calling tools),
    the final text response is returned.

    Falls back to a plain invocation when ``bind_tools`` is not supported.
    """
    if not tools:
        response = llm.invoke([HumanMessage(content=prompt)])
        return str(response.content)

    try:
        llm_with_tools = llm.bind_tools(tools)
    except (NotImplementedError, AttributeError):
        # LLM does not support tool calling — fall back to plain invoke.
        response = llm.invoke([HumanMessage(content=prompt)])
        return str(response.content)

    tools_map = {t.name: t for t in tools}
    messages: list = [HumanMessage(content=prompt)]

    for _ in range(max_rounds + 1):  # +1 for the final response
        response = llm_with_tools.invoke(messages)
        messages.append(response)

        tool_calls = getattr(response, "tool_calls", None)
        if not tool_calls:
            break

        for tc in tool_calls:
            tool_fn = tools_map.get(tc["name"])
            if tool_fn is None:
                result = f"Unknown tool: {tc['name']}"
            else:
                try:
                    result = str(tool_fn.invoke(tc["args"]))
                except Exception as exc:
                    logger.warning("Tool %s failed: %s", tc["name"], exc)
                    result = f"Tool call failed: {exc}"
            messages.append(
                ToolMessage(content=result, tool_call_id=tc["id"])
            )

    return str(response.content)


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
    """Analyses dividend yield and sustainability.

    Has access to a web search tool so the LLM can look up recent
    dividend announcements if the data is insufficient.
    """

    def __init__(self, llm: BaseChatModel) -> None:
        self._llm = llm

    def run(self, symbol: str) -> DividendAnalysis:
        from ph_stocks_advisor.agents.web_search_tools import search_dividend_news

        data = fetch_dividend_info(symbol)
        prompt = DIVIDEND_ANALYSIS_PROMPT.format(
            symbol=symbol, data=data.model_dump_json(indent=2),
            today=get_today().isoformat(),
        )
        analysis = _run_with_tools(self._llm, prompt, [search_dividend_news])
        return DividendAnalysis(data=data, analysis=analysis)


class MovementAgent:
    """Analyses 1-year price trend, volatility, and patterns.

    Has access to a web search tool so the LLM can look up recent
    news to explain significant price movements.
    """

    def __init__(self, llm: BaseChatModel) -> None:
        self._llm = llm

    def run(self, symbol: str) -> MovementAnalysis:
        from ph_stocks_advisor.agents.web_search_tools import search_stock_news

        data = fetch_price_movement(symbol)
        prompt = MOVEMENT_ANALYSIS_PROMPT.format(
            symbol=symbol, data=data.model_dump_json(indent=2),
            today=get_today().isoformat(),
        )
        analysis = _run_with_tools(self._llm, prompt, [search_stock_news])
        return MovementAnalysis(data=data, analysis=analysis)


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
    """Detects price anomalies and flags risk factors.

    Has access to web search tools so the LLM can look up recent
    news and controversies for additional risk context.
    """

    def __init__(self, llm: BaseChatModel) -> None:
        self._llm = llm

    def run(self, symbol: str) -> ControversyAnalysis:
        from ph_stocks_advisor.agents.web_search_tools import (
            search_stock_controversies,
            search_stock_news,
        )

        data = fetch_controversy_info(symbol)
        prompt = CONTROVERSY_ANALYSIS_PROMPT.format(
            symbol=symbol, data=data.model_dump_json(indent=2),
            today=get_today().isoformat(),
        )
        analysis = _run_with_tools(
            self._llm, prompt, [search_stock_news, search_stock_controversies]
        )
        return ControversyAnalysis(data=data, analysis=analysis)
