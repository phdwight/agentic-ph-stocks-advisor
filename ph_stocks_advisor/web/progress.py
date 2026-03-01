"""
Redis Pub/Sub progress publisher for analysis tasks.

Single Responsibility: provides a thin interface for publishing
progress events from the Celery worker, and subscribing to them
from the Flask SSE endpoint.

Events are JSON-encoded dicts published to the Redis channel
``analysis:progress:<task_id>``.  Each event has at minimum:

    {"step": <int>, "label": "<str>", "done": <bool>}

and may include ``verdict``, ``error``, ``report_id``, or ``symbol``.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Generator

import redis as redis_lib

from ph_stocks_advisor.infra.config import get_settings

logger = logging.getLogger(__name__)

# Redis channel prefix for progress events.
_CHANNEL_PREFIX = "analysis:progress:"

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
    """Publish a progress event for *task_id* to Redis Pub/Sub."""
    event: dict[str, Any] = {
        "step": step,
        "label": STEP_LABELS.get(step, f"Step {step}"),
        "done": done,
    }
    if error:
        event["error"] = error
    event.update(extra)

    try:
        r = _get_redis()
        r.publish(_channel(task_id), json.dumps(event))
    except Exception:
        logger.debug(
            "Failed to publish progress for task %s", task_id, exc_info=True
        )


# ---------------------------------------------------------------------------
# Subscriber (called from the Flask SSE endpoint)
# ---------------------------------------------------------------------------


def subscribe_progress(task_id: str) -> Generator[dict[str, Any], None, None]:
    """Yield progress events for *task_id* from Redis Pub/Sub.

    Blocks until a ``done`` event is received or the connection drops.
    Each yielded dict is the deserialized JSON event.
    """
    r = _get_redis()
    pubsub = r.pubsub()
    pubsub.subscribe(_channel(task_id))

    try:
        for message in pubsub.listen():
            if message["type"] != "message":
                continue
            try:
                event = json.loads(message["data"])
            except (json.JSONDecodeError, TypeError):
                continue

            yield event

            if event.get("done"):
                break
    finally:
        pubsub.unsubscribe(_channel(task_id))
        pubsub.close()
