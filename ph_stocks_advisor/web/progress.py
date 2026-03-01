"""
Redis Pub/Sub + state-key progress publisher for analysis tasks.

Single Responsibility: provides a thin interface for publishing
progress events from the Celery worker, and subscribing to them
from the Flask SSE endpoint.

Events are JSON-encoded dicts published to the Redis channel
``analysis:progress:<task_id>``.  Each event has at minimum:

    {"step": <int>, "label": "<str>", "done": <bool>}

and may include ``verdict``, ``error``, ``report_id``, or ``symbol``.

**Race-condition resilience**: every ``publish_progress`` call also
writes the latest event to a Redis key (``analysis:state:<task_id>``)
that persists for 15 minutes.  When a subscriber connects it reads
the stored state first so it never misses events that were published
before the Pub/Sub subscription was established.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Generator

import redis as redis_lib

from ph_stocks_advisor.infra.config import get_settings

logger = logging.getLogger(__name__)

# Redis key/channel prefixes.
_CHANNEL_PREFIX = "analysis:progress:"
_STATE_PREFIX = "analysis:state:"

# How long the stored state key lives (seconds).
_STATE_TTL = 15 * 60  # 15 minutes

# How long subscribe_progress waits without any event before
# falling back to check the stored state key (seconds).
_POLL_INTERVAL = 2.0

# Maximum total time subscribe_progress will block (seconds).
_MAX_WAIT = 10 * 60  # 10 minutes

# Step constants — shared between publisher and frontend.
STEP_QUEUED = 0
STEP_VALIDATING = 1
STEP_FETCHING = 2
STEP_AGENTS = 3
STEP_CONSOLIDATING = 4
STEP_SAVING = 5

STEP_LABELS = {
    STEP_QUEUED: "Queued",
    STEP_VALIDATING: "Validating symbol",
    STEP_FETCHING: "Fetching data",
    STEP_AGENTS: "Running agents",
    STEP_CONSOLIDATING: "Consolidating",
    STEP_SAVING: "Saving report",
}


def _channel(task_id: str) -> str:
    """Return the Redis Pub/Sub channel name for *task_id*."""
    return f"{_CHANNEL_PREFIX}{task_id}"


def _state_key(task_id: str) -> str:
    """Return the Redis key that stores the latest progress snapshot."""
    return f"{_STATE_PREFIX}{task_id}"


def _get_redis() -> redis_lib.Redis:
    return redis_lib.from_url(get_settings().redis_url, decode_responses=True)


# ---------------------------------------------------------------------------
# Publisher (called from the Celery worker)
# ---------------------------------------------------------------------------


def publish_progress(
    task_id: str,
    step: int,
    *,
    done: bool = False,
    error: str | None = None,
    **extra: Any,
) -> None:
    """Publish a progress event for *task_id*.

    The event is both **published** to the Pub/Sub channel (for
    real-time delivery) and **stored** in a Redis key (so late
    subscribers can catch up).
    """
    event: dict[str, Any] = {
        "step": step,
        "label": STEP_LABELS.get(step, f"Step {step}"),
        "done": done,
    }
    if error:
        event["error"] = error
    event.update(extra)

    payload = json.dumps(event)

    try:
        r = _get_redis()
        # Persist latest state so late subscribers can read it.
        r.set(_state_key(task_id), payload, ex=_STATE_TTL)
        # Broadcast to any connected subscribers.
        r.publish(_channel(task_id), payload)
    except Exception:
        logger.debug(
            "Failed to publish progress for task %s", task_id, exc_info=True
        )


# ---------------------------------------------------------------------------
# Subscriber (called from the Flask SSE endpoint)
# ---------------------------------------------------------------------------


def subscribe_progress(task_id: str) -> Generator[dict[str, Any], None, None]:
    """Yield progress events for *task_id*.

    1. Reads the stored state key — if the task is already done the
       stored event is yielded immediately and the generator returns.
    2. Otherwise subscribes to the Pub/Sub channel and polls with a
       short timeout.  Between polls it re-checks the stored state
       key so that events published during the subscription gap are
       never lost.
    3. Automatically stops after ``_MAX_WAIT`` seconds to avoid
       zombie connections.
    """
    r = _get_redis()

    # ── 1. Check stored state (catches events published before we connect) ──
    stored = r.get(_state_key(task_id))
    if stored:
        try:
            event = json.loads(stored)
            yield event
            if event.get("done"):
                return
        except (json.JSONDecodeError, TypeError):
            pass

    # ── 2. Subscribe and poll with timeout ──────────────────────────────────
    pubsub = r.pubsub()
    pubsub.subscribe(_channel(task_id))
    last_step_seen = -1
    deadline = time.monotonic() + _MAX_WAIT

    try:
        while time.monotonic() < deadline:
            msg = pubsub.get_message(timeout=_POLL_INTERVAL)

            if msg and msg["type"] == "message":
                try:
                    event = json.loads(msg["data"])
                except (json.JSONDecodeError, TypeError):
                    continue

                if event.get("step", -1) > last_step_seen:
                    last_step_seen = event["step"]
                    yield event

                if event.get("done"):
                    return
                continue

            # No Pub/Sub message within the poll interval — check the
            # stored state key as a fallback (covers the race window).
            stored = r.get(_state_key(task_id))
            if stored:
                try:
                    event = json.loads(stored)
                except (json.JSONDecodeError, TypeError):
                    continue

                if event.get("step", -1) > last_step_seen:
                    last_step_seen = event["step"]
                    yield event

                if event.get("done"):
                    return

        # Deadline exceeded — emit a synthetic timeout event.
        yield {"step": STEP_SAVING, "label": "Timed out", "done": True,
               "error": "Progress stream timed out. Check status manually."}
    finally:
        pubsub.unsubscribe(_channel(task_id))
        pubsub.close()
