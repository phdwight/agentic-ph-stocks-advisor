"""
LangGraph workflow definition.

Orchestrates the five specialist agents in parallel, then feeds their
results into the consolidator agent for a final verdict.

Open/Closed Principle: new analysis nodes are registered in the
``AGENT_REGISTRY`` list — existing node functions need not change.

Dependency Inversion: the LLM is injected into ``build_graph`` and
closed over in every node, so nodes never call ``get_llm()`` directly.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional, TypedDict

from langchain_core.language_models import BaseChatModel
from langgraph.graph import END, StateGraph

from ph_stocks_advisor.agents.specialists import (
    ControversyAgent,
    DividendAgent,
    MovementAgent,
    PriceAgent,
    ValuationAgent,
)
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
# Agent registry — add entries here to wire new specialist agents.
#
# Each tuple: (node_name, state_key, agent_class)
#
# The agent_class must accept a BaseChatModel in __init__ and expose
# a .run(symbol) method returning a Pydantic model.  The state_key
# must match a field in GraphState.
# ---------------------------------------------------------------------------

AgentEntry = tuple[str, str, type]

AGENT_REGISTRY: list[AgentEntry] = [
    ("price_agent", "price_analysis", PriceAgent),
    ("dividend_agent", "dividend_analysis", DividendAgent),
    ("movement_agent", "movement_analysis", MovementAgent),
    ("valuation_agent", "valuation_analysis", ValuationAgent),
    ("controversy_agent", "controversy_analysis", ControversyAgent),
]


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
# Node factory — creates closures that share the injected LLM
# ---------------------------------------------------------------------------


def _make_specialist_node(
    agent_class: type,
    state_key: str,
    llm: BaseChatModel,
) -> Callable[[GraphState], GraphState]:
    """Return a node function that runs *agent_class* and writes *state_key*."""

    def _node(state: GraphState) -> GraphState:
        try:
            agent = agent_class(llm)
            result = agent.run(state["symbol"])
            return {state_key: result}  # type: ignore[return-value]
        except Exception as exc:
            logger.error(
                "%s failed for %s: %s",
                agent_class.__name__,
                state["symbol"],
                exc,
            )
            return {}  # type: ignore[return-value]

    return _node


def _make_validate_node() -> Callable[[GraphState], GraphState]:
    """Return the validation gate node."""

    def _validate(state: GraphState) -> GraphState:
        symbol = state["symbol"]
        try:
            validate_symbol(symbol)
            return {}  # type: ignore[return-value]
        except SymbolNotFoundError as exc:
            return {"error": str(exc)}  # type: ignore[return-value]

    return _validate


def _make_consolidate_node(llm: BaseChatModel) -> Callable[[GraphState], GraphState]:
    """Return the consolidator node."""

    def _consolidate(state: GraphState) -> GraphState:
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

    return _consolidate


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------


def build_graph(llm: BaseChatModel | None = None):
    """
    Construct and compile the LangGraph workflow.

    Parameters
    ----------
    llm : BaseChatModel | None
        The language model to inject into every agent node.  When ``None``
        the default LLM from ``get_llm()`` is used (convenient for CLI).

    Topology:
        START ──┬── price_agent ────────┐
                ├── dividend_agent ─────┤
                ├── movement_agent ─────┼── consolidator ── END
                ├── valuation_agent ────┤
                └── controversy_agent ──┘
    """
    if llm is None:
        from ph_stocks_advisor.infra.config import get_llm

        llm = get_llm()

    workflow = StateGraph(GraphState)

    # Validation gate — runs first to ensure the symbol exists
    workflow.add_node("validate", _make_validate_node())

    # Dynamically register specialist nodes from the registry
    specialist_names: list[str] = []
    for node_name, state_key, agent_class in AGENT_REGISTRY:
        node_fn = _make_specialist_node(agent_class, state_key, llm)
        workflow.add_node(node_name, node_fn)
        specialist_names.append(node_name)

    # Consolidator
    workflow.add_node("consolidator", _make_consolidate_node(llm))

    # START → validate
    workflow.add_edge("__start__", "validate")

    # Conditional: if validation set an error, go straight to END;
    # otherwise fan-out to all specialist agents.
    def _route_after_validation(state: GraphState) -> list[str] | str:
        if state.get("error"):
            return END
        return specialist_names

    workflow.add_conditional_edges("validate", _route_after_validation)

    # Fan-in: all specialists feed into the consolidator
    for node_name in specialist_names:
        workflow.add_edge(node_name, "consolidator")

    # Consolidator produces the final output
    workflow.add_edge("consolidator", END)

    return workflow.compile()


def run_analysis(symbol: str, llm: BaseChatModel | None = None) -> dict[str, Any]:
    """
    Run the full multi-agent analysis for a PSE stock symbol.

    Parameters
    ----------
    symbol : str
        PSE ticker symbol (e.g. "TEL", "BDO", "SM", "ALI").
    llm : BaseChatModel | None
        Optional LLM override.  Uses the default ``get_llm()`` when ``None``.

    Returns
    -------
    dict
        The final state dict containing all analyses and the final report.
    """
    graph = build_graph(llm=llm)
    initial_state: GraphState = {"symbol": symbol.upper().replace(".PS", "")}
    return graph.invoke(initial_state)
