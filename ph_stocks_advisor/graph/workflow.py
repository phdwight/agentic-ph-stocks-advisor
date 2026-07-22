"""
LangGraph workflow definition.

Orchestrates the six specialist agents in parallel, then feeds their
results into the consolidator agent for a final verdict.

Open/Closed Principle: new analysis nodes are registered in the
``AGENT_REGISTRY`` list — existing node functions need not change.

Dependency Inversion: the LLM is injected into ``build_graph`` and
closed over in every node, so nodes never call ``get_llm()`` directly.
"""

from __future__ import annotations

import logging
import operator
from collections.abc import Callable
from typing import Annotated, Any, Required, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph

from ph_stocks_advisor.agents.consolidator import ConsolidatorAgent
from ph_stocks_advisor.agents.specialists import (
    ControversyAgent,
    DividendAgent,
    EmptyAgentDataError,
    MovementAgent,
    PriceAgent,
    SentimentAgent,
    ValuationAgent,
)
from ph_stocks_advisor.data.models import (
    AdvisorState,
    ControversyAnalysis,
    ControversyInfo,
    DividendAnalysis,
    DividendInfo,
    FairValueEstimate,
    FinalReport,
    MovementAnalysis,
    PriceAnalysis,
    PriceMovement,
    SentimentAnalysis,
    SentimentInfo,
    StockPrice,
    ValuationAnalysis,
)
from ph_stocks_advisor.data.tools import SymbolNotFoundError, validate_symbol
from ph_stocks_advisor.infra.tracing import build_langfuse_config, flush_langfuse

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
    ("sentiment_agent", "sentiment_analysis", SentimentAgent),
]


# ---------------------------------------------------------------------------
# State schema — TypedDict with individually-keyed channels so that
# parallel (fan-out) nodes can each write to their own key without conflict.
# ---------------------------------------------------------------------------


def _keep_first_error(existing: str | None, incoming: str | None) -> str | None:
    """Reducer for the shared ``error`` channel.

    The six specialist agents run as a parallel fan-out, so when several
    fail in the same superstep (e.g. an OpenAI outage or bad API key), they
    all write ``error`` at once. A plain LastValue channel rejects that with
    ``InvalidUpdateError``; this reducer instead keeps the first error seen,
    which is enough to abort the pipeline deterministically.
    """
    return existing if existing is not None else incoming


class GraphState(TypedDict, total=False):
    symbol: Required[str]
    error: Annotated[str | None, _keep_first_error]
    # State keys of dimensions whose specialist could not produce real
    # data this run (no data exists, or a transient failure). Appended by
    # parallel nodes, hence the list reducer. The consolidator excludes
    # these dimensions from the verdict score and the report must state
    # the gap; if ALL dimensions are gaps the run aborts.
    data_gaps: Annotated[list[str], operator.add]
    price_analysis: PriceAnalysis | None
    dividend_analysis: DividendAnalysis | None
    movement_analysis: MovementAnalysis | None
    valuation_analysis: ValuationAnalysis | None
    controversy_analysis: ControversyAnalysis | None
    sentiment_analysis: SentimentAnalysis | None
    final_report: FinalReport | None


# ---------------------------------------------------------------------------
# Fallback analyses — used when a specialist cannot produce real data.
# A failing dimension must NOT stop the whole run: the pipeline continues,
# the dimension is excluded from the verdict score, and the report states
# the gap and its effect (e.g. a ticker that simply pays no dividends).
# ---------------------------------------------------------------------------

_DIMENSION_LABELS = {
    "price_analysis": "price",
    "dividend_analysis": "dividend",
    "movement_analysis": "price-movement",
    "valuation_analysis": "valuation",
    "controversy_analysis": "controversy/risk",
    "sentiment_analysis": "sentiment/global-events",
}


def _fallback_analysis(state_key: str, symbol: str, *, transient: bool):
    """Build a placeholder analysis for a dimension without real data.

    The ``analysis`` text flows into the consolidation prompt and the saved
    section, so it is written for both audiences: it tells the reader what
    is missing and instructs the consolidator to exclude the dimension.
    """
    label = _DIMENSION_LABELS.get(state_key, state_key)
    if transient:
        note = (
            f"DATA UNAVAILABLE: {label.capitalize()} data for {symbol} could not be "
            "retrieved this run due to a temporary error. This dimension was "
            "excluded from the verdict score; state this gap and its effect in the report."
        )
    else:
        note = (
            f"DATA UNAVAILABLE: {symbol} has no {label} history or data on record "
            "(for dividends this usually means the stock does not pay dividends). "
            "This dimension was excluded from the verdict score; state this gap "
            "and its effect plainly in the report."
        )
    builders = {
        "price_analysis": lambda: PriceAnalysis(data=StockPrice(symbol=symbol, current_price=0.0), analysis=note),
        "dividend_analysis": lambda: DividendAnalysis(data=DividendInfo(symbol=symbol), analysis=note),
        "movement_analysis": lambda: MovementAnalysis(data=PriceMovement(symbol=symbol), analysis=note),
        "valuation_analysis": lambda: ValuationAnalysis(data=FairValueEstimate(symbol=symbol), analysis=note),
        "controversy_analysis": lambda: ControversyAnalysis(data=ControversyInfo(symbol=symbol), analysis=note),
        "sentiment_analysis": lambda: SentimentAnalysis(data=SentimentInfo(symbol=symbol), analysis=note),
    }
    return builders[state_key]()


# ---------------------------------------------------------------------------
# Node factory — creates closures that share the injected LLM
# ---------------------------------------------------------------------------


def _make_specialist_node(
    agent_class: type,
    state_key: str,
    llm: BaseChatModel,
    task_id: str | None = None,
) -> Callable[[GraphState], GraphState]:
    """Return a node function that runs *agent_class* and writes *state_key*."""

    def _node(state: GraphState) -> GraphState:
        # If validation already aborted the run (e.g. unknown symbol),
        # short-circuit to avoid wasting LLM calls.
        if state.get("error"):
            return {}  # type: ignore[return-value]

        def _publish_agent_done() -> None:
            """Publish per-agent completion to the SSE stream."""
            if task_id:
                from ph_stocks_advisor.web.progress import (
                    STEP_AGENTS,
                    publish_progress,
                )

                publish_progress(
                    task_id,
                    STEP_AGENTS,
                    agent=agent_class.__name__,
                )

        try:
            agent = agent_class(llm)
            result = agent.run(state["symbol"])
            _publish_agent_done()
            return {state_key: result}  # type: ignore[return-value]
        except EmptyAgentDataError:
            # No data exists for this dimension (e.g. a stock that pays no
            # dividends). That is information, not a failure — continue with
            # a placeholder so the report can state the gap; the dimension
            # is excluded from the verdict score via ``data_gaps``.
            logger.warning(
                "%s has no data for %s — continuing without this dimension.",
                agent_class.__name__,
                state["symbol"],
            )
            fallback = _fallback_analysis(state_key, state["symbol"], transient=False)
            _publish_agent_done()
            return {state_key: fallback, "data_gaps": [state_key]}  # type: ignore[return-value]
        except Exception as exc:
            # Transient failure (MCP timeout, network blip, upstream API
            # error). Also non-fatal: continue with a placeholder, exclude
            # the dimension from the score, and let the report state the
            # gap. The prompt forbids inventing numbers for gap dimensions,
            # and a run where EVERY dimension failed still aborts in the
            # consolidate node (systemic-failure guard).
            logger.error(
                "%s failed for %s: %s — continuing without this dimension.",
                agent_class.__name__,
                state["symbol"],
                exc,
                exc_info=True,
            )
            fallback = _fallback_analysis(state_key, state["symbol"], transient=True)
            _publish_agent_done()
            return {state_key: fallback, "data_gaps": [state_key]}  # type: ignore[return-value]

    return _node


def _make_validate_node(
    task_id: str | None = None,
) -> Callable[[GraphState], GraphState]:
    """Return the validation gate node."""

    def _validate(state: GraphState) -> GraphState:
        symbol = state["symbol"]

        if task_id:
            from ph_stocks_advisor.web.progress import (
                STEP_VALIDATING,
                publish_progress,
            )

            publish_progress(task_id, STEP_VALIDATING)

        try:
            validate_symbol(symbol)
            return {}  # type: ignore[return-value]
        except SymbolNotFoundError as exc:
            return {"error": str(exc)}  # type: ignore[return-value]

    return _validate


def _make_consolidate_node(
    llm: BaseChatModel,
    task_id: str | None = None,
) -> Callable[[GraphState], GraphState]:
    """Return the consolidator node."""

    def _consolidate(state: GraphState) -> GraphState:
        # Honour any upstream error (e.g. a failed symbol validation):
        # skip consolidation so the caller receives the original error.
        if state.get("error"):
            return {}  # type: ignore[return-value]

        # Systemic-failure guard: individual data gaps are tolerated (the
        # report states them), but if EVERY dimension failed there is
        # nothing real to consolidate — abort instead of fabricating.
        gaps = state.get("data_gaps") or []
        if len(set(gaps)) >= len(AGENT_REGISTRY):
            return {  # type: ignore[return-value]
                "error": (f"No specialist agent could produce data for {state['symbol']} — analysis aborted.")
            }

        if task_id:
            from ph_stocks_advisor.web.progress import (
                STEP_CONSOLIDATING,
                publish_progress,
            )

            publish_progress(task_id, STEP_CONSOLIDATING)

        agent = ConsolidatorAgent(llm)
        advisor_state = AdvisorState(
            symbol=state["symbol"],
            price_analysis=state.get("price_analysis"),
            dividend_analysis=state.get("dividend_analysis"),
            movement_analysis=state.get("movement_analysis"),
            valuation_analysis=state.get("valuation_analysis"),
            controversy_analysis=state.get("controversy_analysis"),
            sentiment_analysis=state.get("sentiment_analysis"),
            data_gaps=sorted(set(gaps)),
        )
        result = agent.run(advisor_state)
        return {"final_report": result}  # type: ignore[return-value]

    return _consolidate


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------


def _build_graph_impl(
    llm: BaseChatModel | None = None,
    mini_llm: BaseChatModel | None = None,
    task_id: str | None = None,
):
    """
    Internal graph builder used by both the CLI and LangGraph Studio.

    Parameters
    ----------
    llm : BaseChatModel | None
        The *primary* (heavy) language model used for the consolidator
        agent.  When ``None`` the default from ``get_llm()`` is used.
    mini_llm : BaseChatModel | None
        A lighter language model used for the specialist agents to
        reduce cost.  When ``None`` the default from ``get_mini_llm()``
        is used.
    task_id : str | None
        Optional Celery task ID.  When provided, nodes publish real-time
        progress events to Redis Pub/Sub for the SSE stream.

    Topology:
        START ──┬── price_agent ────────┐
                ├── dividend_agent ─────┤
                ├── movement_agent ─────┤
                ├── valuation_agent ────┼── consolidator ── END
                ├── controversy_agent ──┤
                └── sentiment_agent ────┘
    """
    if llm is None:
        from ph_stocks_advisor.infra.config import get_llm

        llm = get_llm()

    # ``mini_llm``, when explicitly supplied (tests), overrides every
    # specialist. Left as ``None``, each specialist resolves its own
    # per-agent model from ``AGENT_LLM_SPECS`` so agents can run on
    # different providers/tiers.
    specialist_override = mini_llm

    workflow = StateGraph(GraphState)

    # Validation gate — runs first to ensure the symbol exists
    workflow.add_node("validate", _make_validate_node(task_id=task_id))  # type: ignore[arg-type]

    # Dynamically register specialist nodes from the registry
    specialist_names: list[str] = []
    for node_name, state_key, agent_class in AGENT_REGISTRY:
        if specialist_override is not None:
            node_llm: BaseChatModel = specialist_override
        else:
            from ph_stocks_advisor.infra.config import get_agent_llm

            node_llm = get_agent_llm(node_name)
        node_fn = _make_specialist_node(agent_class, state_key, node_llm, task_id=task_id)
        workflow.add_node(node_name, node_fn)  # type: ignore[arg-type]
        specialist_names.append(node_name)

    # Consolidator
    workflow.add_node("consolidator", _make_consolidate_node(llm, task_id=task_id))  # type: ignore[arg-type]

    # START → validate
    workflow.add_edge("__start__", "validate")

    # Conditional: if validation set an error, go straight to END;
    # otherwise fan-out to all specialist agents.
    def _route_after_validation(state: GraphState) -> list[str] | str:
        if state.get("error"):
            return END
        return specialist_names

    # Provide an explicit path_map so the graph visualizer can render
    # all possible edges from the conditional branch.
    path_map: dict[str, str] = {name: name for name in specialist_names}
    path_map[END] = END
    workflow.add_conditional_edges("validate", _route_after_validation, path_map=path_map)  # type: ignore[arg-type]

    # Fan-in: all specialists feed into the consolidator
    for node_name in specialist_names:
        workflow.add_edge(node_name, "consolidator")

    # Consolidator produces the final output
    workflow.add_edge("consolidator", END)

    return workflow.compile()


def build_graph(config: RunnableConfig) -> Any:
    """LangGraph Studio / CLI entry point.

    LangGraph Studio requires the graph factory to accept exactly one
    ``RunnableConfig`` argument.  This thin wrapper satisfies that
    contract and delegates to :func:`_build_graph_impl`.
    """
    return _build_graph_impl()


def run_analysis(
    symbol: str,
    llm: BaseChatModel | None = None,
    mini_llm: BaseChatModel | None = None,
    task_id: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    """
    Run the full multi-agent analysis for a PSE stock symbol.

    Parameters
    ----------
    symbol : str
        PSE ticker symbol (e.g. "TEL", "BDO", "SM", "ALI").
    llm : BaseChatModel | None
        Optional primary (heavy) LLM override for the consolidator.
        Uses the default ``get_llm()`` when ``None``.
    mini_llm : BaseChatModel | None
        Optional lighter LLM override for specialist agents.
        Uses the default ``get_mini_llm()`` when ``None``.
    task_id : str | None
        Optional Celery task ID.  When provided, progress events are
        published to Redis Pub/Sub for the SSE stream and the ID is
        used as the Langfuse ``session_id`` so all spans for one job
        group together.
    user_id : str | None
        Authenticated user identifier; attached to the Langfuse trace
        as ``user_id`` for per-user filtering and cost attribution.

    Returns
    -------
    dict
        The final state dict containing all analyses and the final report.
    """
    normalized_symbol = symbol.upper().replace(".PS", "")
    graph = _build_graph_impl(llm=llm, mini_llm=mini_llm, task_id=task_id)
    initial_state: GraphState = {"symbol": normalized_symbol}
    invoke_config: RunnableConfig = build_langfuse_config(  # type: ignore[assignment]
        run_name="stock-analysis",
        user_id=user_id,
        session_id=task_id,
        tags=["stock-analysis", "ph-stocks-advisor"],
        metadata={"symbol": normalized_symbol},
    )
    try:
        return graph.invoke(initial_state, config=invoke_config)
    finally:
        flush_langfuse()
