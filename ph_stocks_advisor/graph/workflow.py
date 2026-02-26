"""
LangGraph workflow definition.

Orchestrates the five specialist agents in parallel, then feeds their
results into the consolidator agent for a final verdict.

Open/Closed Principle: new analysis nodes can be added without modifying
existing node functions — just register them in `build_graph`.
"""

from __future__ import annotations

import logging
from typing import Any, Optional, TypedDict

from langgraph.graph import END, StateGraph

from ph_stocks_advisor.agents.specialists import (
    ControversyAgent,
    DividendAgent,
    MovementAgent,
    PriceAgent,
    ValuationAgent,
)
from ph_stocks_advisor.infra.config import get_llm
from ph_stocks_advisor.agents.consolidator import ConsolidatorAgent
from ph_stocks_advisor.data.models import (
    AdvisorState,
    ControversyAnalysis,
    DividendAnalysis,
    FinalReport,
    MovementAnalysis,
    PriceAnalysis,
    ValuationAnalysis,
)
from ph_stocks_advisor.data.tools import SymbolNotFoundError, validate_symbol

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State schema — TypedDict with individually-keyed channels so that
# parallel (fan-out) nodes can each write to their own key without conflict.
# ---------------------------------------------------------------------------


class GraphState(TypedDict, total=False):
    symbol: str
    error: Optional[str]
    price_analysis: Optional[PriceAnalysis]
    dividend_analysis: Optional[DividendAnalysis]
    movement_analysis: Optional[MovementAnalysis]
    valuation_analysis: Optional[ValuationAnalysis]
    controversy_analysis: Optional[ControversyAnalysis]
    final_report: Optional[FinalReport]


# ---------------------------------------------------------------------------
# Node functions — each wraps one agent call
# ---------------------------------------------------------------------------


def _validate_node(state: GraphState) -> GraphState:
    """Validate the symbol exists on Yahoo Finance before running agents."""
    symbol = state["symbol"]
    try:
        validate_symbol(symbol)
        return {}  # type: ignore[return-value]
    except SymbolNotFoundError as exc:
        return {"error": str(exc)}  # type: ignore[return-value]


def _price_node(state: GraphState) -> GraphState:
    try:
        llm = get_llm()
        agent = PriceAgent(llm)
        result = agent.run(state["symbol"])
        return {"price_analysis": result}  # type: ignore[return-value]
    except Exception as exc:
        logger.error("price_agent failed for %s: %s", state["symbol"], exc)
        return {}  # type: ignore[return-value]


def _dividend_node(state: GraphState) -> GraphState:
    try:
        llm = get_llm()
        agent = DividendAgent(llm)
        result = agent.run(state["symbol"])
        return {"dividend_analysis": result}  # type: ignore[return-value]
    except Exception as exc:
        logger.error("dividend_agent failed for %s: %s", state["symbol"], exc)
        return {}  # type: ignore[return-value]


def _movement_node(state: GraphState) -> GraphState:
    try:
        llm = get_llm()
        agent = MovementAgent(llm)
        result = agent.run(state["symbol"])
        return {"movement_analysis": result}  # type: ignore[return-value]
    except Exception as exc:
        logger.error("movement_agent failed for %s: %s", state["symbol"], exc)
        return {}  # type: ignore[return-value]


def _valuation_node(state: GraphState) -> GraphState:
    try:
        llm = get_llm()
        agent = ValuationAgent(llm)
        result = agent.run(state["symbol"])
        return {"valuation_analysis": result}  # type: ignore[return-value]
    except Exception as exc:
        logger.error("valuation_agent failed for %s: %s", state["symbol"], exc)
        return {}  # type: ignore[return-value]


def _controversy_node(state: GraphState) -> GraphState:
    try:
        llm = get_llm()
        agent = ControversyAgent(llm)
        result = agent.run(state["symbol"])
        return {"controversy_analysis": result}  # type: ignore[return-value]
    except Exception as exc:
        logger.error("controversy_agent failed for %s: %s", state["symbol"], exc)
        return {}  # type: ignore[return-value]


def _consolidate_node(state: GraphState) -> GraphState:
    llm = get_llm()
    agent = ConsolidatorAgent(llm)
    advisor_state = AdvisorState(
        symbol=state["symbol"],
        price_analysis=state.get("price_analysis"),
        dividend_analysis=state.get("dividend_analysis"),
        movement_analysis=state.get("movement_analysis"),
        valuation_analysis=state.get("valuation_analysis"),
        controversy_analysis=state.get("controversy_analysis"),
    )
    result = agent.run(advisor_state)
    return {"final_report": result}  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

def build_graph():
    """
    Construct and compile the LangGraph workflow.

    Topology:
        START ──┬── price_agent ────────┐
                ├── dividend_agent ─────┤
                ├── movement_agent ─────┼── consolidator ── END
                ├── valuation_agent ────┤
                └── controversy_agent ──┘
    """
    workflow = StateGraph(GraphState)

    # Validation gate — runs first to ensure the symbol exists
    workflow.add_node("validate", _validate_node)

    # Register specialist nodes
    workflow.add_node("price_agent", _price_node)
    workflow.add_node("dividend_agent", _dividend_node)
    workflow.add_node("movement_agent", _movement_node)
    workflow.add_node("valuation_agent", _valuation_node)
    workflow.add_node("controversy_agent", _controversy_node)
    workflow.add_node("consolidator", _consolidate_node)

    # START → validate
    workflow.add_edge("__start__", "validate")

    # Conditional: if validation set an error, go straight to END;
    # otherwise fan-out to all specialist agents.
    specialist_nodes = [
        "price_agent",
        "dividend_agent",
        "movement_agent",
        "valuation_agent",
        "controversy_agent",
    ]

    def _route_after_validation(state: GraphState) -> list[str] | str:
        if state.get("error"):
            return END
        return specialist_nodes

    workflow.add_conditional_edges("validate", _route_after_validation)

    # Fan-in: all specialists feed into the consolidator
    for node_name in specialist_nodes:
        workflow.add_edge(node_name, "consolidator")

    # Consolidator produces the final output
    workflow.add_edge("consolidator", END)

    return workflow.compile()


def run_analysis(symbol: str) -> dict[str, Any]:
    """
    Run the full multi-agent analysis for a PSE stock symbol.

    Args:
        symbol: PSE ticker symbol (e.g. "TEL", "BDO", "SM", "ALI").

    Returns:
        The final state dict containing all analyses and the final report.
    """
    graph = build_graph()
    initial_state: GraphState = {"symbol": symbol.upper()}
    result = graph.invoke(initial_state)
    return result
