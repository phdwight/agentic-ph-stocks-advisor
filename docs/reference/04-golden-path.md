# 04 · Golden path

One request, traced through every layer: `analyse("TEL")` after market close, no report since the last cutoff.

## 1 · `POST /analyze` — `web/app.py`

Session user resolved (`user_id` = email). A fresh run costs a rate-limit slot — reserved atomically, **released if the run fails**. Market open, or a report newer than the last 15:00 cutoff? Serve that instead — no task.

## 2 · `SET NX analysis:inflight:TEL` — ★ one run per ticker

The first requester claims the ticker with a pre-generated task id and queues `analyse_stock`. Anyone else asking for TEL in the meantime gets the **same** task id and attaches to its SSE stream — snapshot first, then live events.

## 3 · `run_analysis("TEL")` — worker · `graph/workflow.py`

The validate gate calls `validate_symbol` via MCP (tri-state: listed / not-listed / *can't-tell* — an upstream outage never brands a real ticker invalid). Errors here end the run with a user-readable reason.

## 4 · Fan-out → six specialists — `agents/specialists.py`

Price, dividend, movement, valuation, controversy, sentiment — each with its own tier/provider, each fetching exclusively through MCP tools. Progress lands on the SSE stream as each phase starts.

## 5 · Consolidator → `FinalReport` — `agents/consolidator.py`

Large-tier structured output: summary sections, six sub-scores → weighted 0–100 score → five-band verdict; the binary BUY/NOT BUY is derived at 60 for compatibility.

## 6 · Save → SSE done → email — `web/tasks.py`

`ReportRecord` persisted; the completion event carries `report_id`; the requesting user gets the Tala-styled result email. **No email failure of any kind may interrupt the flow** — logged, never raised. The inflight claim clears in a `finally`.

## The completion event

What the SSE stream (and the task result) carries:

```json
{
  "step":      5,        // Saving — the last of six progress phases
  "done":      true,
  "symbol":    "TEL",
  "verdict":   "BUY",    // legacy binary — kept for compatibility
  "score":     72,       // 0–100; the five-band label drives the UI
  "report_id": 123       // → /report-by-id/123 · /report/TEL
}
```
