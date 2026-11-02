# graph

## Overview
Defines the LangGraph multi-agent orchestration workflow for PSE stock analysis, coordinating symbol validation, parallel fan-out across six specialist agents, fallback/gap handling, and final verdict consolidation with optional SSE progress streaming and Langfuse tracing.

## Interface
- `[workflow.py:AgentEntry]` - Type alias `tuple[str, str, type]` representing `(node_name, state_key, agent_class)` in the specialist registry.
- `[workflow.py:AGENT_REGISTRY]` - List of six specialist agent registrations (`price_agent`, `dividend_agent`, `movement_agent`, `valuation_agent`, `controversy_agent`, `sentiment_agent`) wired into the fan-out graph.
- `[workflow.py:GraphState]` - `TypedDict` state schema for the LangGraph workflow holding `symbol`, `error` (with `_keep_first_error` reducer), `data_gaps` (with `operator.add` list reducer), individual specialist analysis fields, and `final_report`.
- `[workflow.py:build_graph]` - LangGraph Studio / CLI entry point accepting a single `RunnableConfig` argument and delegating to `_build_graph_impl()`.
- `[workflow.py:run_analysis]` - Executes the full compiled multi-agent `StateGraph` for a given stock symbol with optional LLM overrides, Celery `task_id`, and `user_id`, returning the final `GraphState` dictionary.

## Invariants
- `[workflow.py:AGENT_REGISTRY]` - Each registered `agent_class` must accept a `BaseChatModel` in `__init__` and expose `fetch`/`analyze`/`wrap` (or `.run`) methods, and its `state_key` must match a field in `GraphState`.
- `[workflow.py:_keep_first_error]` - Parallel specialist failures writing to the shared `error` channel preserve the first non-`None` error encountered to prevent LangGraph `InvalidUpdateError` during fan-out supersteps.
- `[workflow.py:run_analysis]` - Input `symbol` is normalized via `symbol.upper().replace('.PS', '')` before invoking the graph.
- `[workflow.py:_route_after_validation]` - If the `validate` node sets `state['error']`, the workflow routes directly to `END` and skips all specialist and consolidator nodes.
- `[workflow.py:_make_specialist_node]` - Non-auth/quota `EmptyAgentDataError` or data fetch failures do not abort the run; they populate a placeholder analysis via `_fallback_analysis` and append `state_key` to `data_gaps`, whereas narration failures preserve fetched data via `agent.wrap(data, note)` and append `state_key` to `data_gaps`. Auth/quota errors detected by `friendly_llm_error` set `state['error']` to abort the run.
- `[workflow.py:_make_consolidate_node]` - Skips consolidation if `state.get('error')` is set, and aborts with an error if all registered specialists failed (`len(set(gaps)) >= len(AGENT_REGISTRY)`); otherwise deduplicates and sorts `data_gaps` before passing `AdvisorState` to `ConsolidatorAgent`.

## Side Effects
- `[workflow.py:_make_validate_node]` - Calls `validate_symbol(symbol)` against external market data tools and, when `task_id` is provided, publishes `STEP_VALIDATING` progress events to Redis Pub/Sub via `publish_progress`.
- `[workflow.py:_make_specialist_node]` - Instantiates specialist agents that perform external market/news data fetches and LLM calls, and publishes `STEP_AGENTS` completion events to Redis Pub/Sub via `publish_progress` when `task_id` is provided.
- `[workflow.py:_make_consolidate_node]` - Publishes `STEP_CONSOLIDATING` progress events via `publish_progress` when `task_id` is provided and invokes `ConsolidatorAgent.run()` against the primary LLM.
- `[workflow.py:_build_graph_impl]` - Lazily initializes default LLMs via `get_llm()` and `get_agent_llm(node_name)` when `llm` or `mini_llm` overrides are not provided.
- `[workflow.py:run_analysis]` - Builds Langfuse tracing callbacks via `build_langfuse_config`, invokes the compiled `StateGraph`, and unconditionally calls `flush_langfuse()` in a `finally` block.

## Verification
- Run: `pytest`
