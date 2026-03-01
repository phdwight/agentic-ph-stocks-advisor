"""
Tests for the SSE progress streaming feature.

Covers:
- ``progress.py`` — publish / subscribe via Redis Pub/Sub
- ``/stream/<task_id>`` — Flask SSE endpoint
- Workflow node progress publishing
"""

from __future__ import annotations

import json
import threading
from unittest.mock import MagicMock, patch

import pytest

import ph_stocks_advisor.web.app as _app_mod
import ph_stocks_advisor.web.progress as progress_mod
import ph_stocks_advisor.web.tasks as _tasks_mod
from ph_stocks_advisor.web.progress import (
    STEP_AGENTS,
    STEP_CONSOLIDATING,
    STEP_FETCHING,
    STEP_LABELS,
    STEP_QUEUED,
    STEP_SAVING,
    STEP_VALIDATING,
    publish_progress,
)


# ---------------------------------------------------------------------------
# In-memory Redis Pub/Sub fake
# ---------------------------------------------------------------------------


class FakeRedisPubSub:
    """Minimal Pub/Sub fake that works with ``subscribe_progress``."""

    def __init__(self, store: "FakeRedisWithPubSub"):
        self._store = store
        self._channels: list[str] = []
        self._msg_queue: list[dict] = []

    def subscribe(self, channel: str) -> None:
        self._channels.append(channel)
        # Drain any messages already published to this channel.
        for msg_data in self._store._drain(channel):
            self._msg_queue.append({"type": "message", "data": msg_data})

    def get_message(self, timeout: float = 0) -> dict | None:
        """Return the next queued message, or None."""
        if self._msg_queue:
            return self._msg_queue.pop(0)
        return None

    def listen(self):
        """Yield messages that were published while we are subscribed."""
        # Yield initial subscribe confirmation (skipped by subscriber).
        yield {"type": "subscribe", "data": None}
        # Then drain any queued messages.
        for channel in self._channels:
            for msg in self._store._drain(channel):
                yield {"type": "message", "data": msg}

    def unsubscribe(self, channel: str) -> None:
        self._channels = [c for c in self._channels if c != channel]

    def close(self) -> None:
        pass


class FakeRedisWithPubSub:
    """In-memory Redis that supports Pub/Sub plus basic get/set/incr."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._pubsub_queues: dict[str, list[str]] = {}

    # Basic Redis interface
    def get(self, key: str) -> str | None:
        return self._store.get(key)

    def set(self, key: str, value: str, ex: int | None = None) -> None:  # noqa: A003
        self._store[key] = value

    def incr(self, key: str) -> int:
        val = int(self._store.get(key, 0)) + 1
        self._store[key] = str(val)
        return val

    def expire(self, key: str, seconds: int) -> None:
        pass

    def delete(self, key: str) -> None:
        self._store.pop(key, None)

    def scan_iter(self, pattern: str) -> list[str]:
        import fnmatch
        return [k for k in self._store if fnmatch.fnmatch(k, pattern)]

    # Pub/Sub interface
    def publish(self, channel: str, message: str) -> int:
        self._pubsub_queues.setdefault(channel, []).append(message)
        return 1

    def pubsub(self) -> FakeRedisPubSub:
        return FakeRedisPubSub(self)

    def _drain(self, channel: str) -> list[str]:
        msgs = self._pubsub_queues.pop(channel, [])
        return msgs


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_redis():
    return FakeRedisWithPubSub()


@pytest.fixture
def client(fake_redis, monkeypatch):
    """Flask test client with external deps mocked."""
    from ph_stocks_advisor.infra.config import get_settings

    monkeypatch.delenv("ENTRA_CLIENT_ID", raising=False)
    monkeypatch.delenv("ENTRA_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)

    get_settings.cache_clear()
    s = get_settings()
    s.entra_client_id = ""
    s.entra_client_secret = ""
    s.google_client_id = ""
    s.google_client_secret = ""

    mock_repo = MagicMock()
    mock_repo.get_latest_by_symbol.return_value = None
    mock_repo.list_recent_symbols.return_value = []

    with (
        patch.object(_app_mod, "get_repository", return_value=mock_repo),
        patch.object(_app_mod, "_get_redis", return_value=fake_redis),
    ):
        app = _app_mod.create_app()
        app.config["TESTING"] = True
        with app.test_client() as c:
            yield c

    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Tests — progress publisher
# ---------------------------------------------------------------------------


class TestPublishProgress:
    """Verify publish_progress sends JSON events to the correct channel."""

    def test_publishes_to_correct_channel(self, fake_redis):
        with patch.object(progress_mod, "_get_redis", return_value=fake_redis):
            publish_progress("task-123", STEP_FETCHING)

        channel = "analysis:progress:task-123"
        assert channel in fake_redis._pubsub_queues
        msgs = fake_redis._pubsub_queues[channel]
        assert len(msgs) == 1
        event = json.loads(msgs[0])
        assert event["step"] == STEP_FETCHING
        assert event["label"] == STEP_LABELS[STEP_FETCHING]
        assert event["done"] is False

        # Also stores latest state in a Redis key.
        state = fake_redis.get("analysis:state:task-123")
        assert state is not None
        assert json.loads(state)["step"] == STEP_FETCHING

    def test_done_event_includes_extra_fields(self, fake_redis):
        with patch.object(progress_mod, "_get_redis", return_value=fake_redis):
            publish_progress(
                "task-456",
                STEP_SAVING,
                done=True,
                symbol="TEL",
                verdict="BUY",
                report_id=42,
            )

        msgs = fake_redis._pubsub_queues["analysis:progress:task-456"]
        event = json.loads(msgs[0])
        assert event["done"] is True
        assert event["symbol"] == "TEL"
        assert event["verdict"] == "BUY"
        assert event["report_id"] == 42

    def test_error_event(self, fake_redis):
        with patch.object(progress_mod, "_get_redis", return_value=fake_redis):
            publish_progress(
                "task-err", STEP_SAVING, done=True, error="LLM timeout"
            )

        msgs = fake_redis._pubsub_queues["analysis:progress:task-err"]
        event = json.loads(msgs[0])
        assert event["done"] is True
        assert event["error"] == "LLM timeout"

    def test_redis_failure_does_not_raise(self):
        """publish_progress must not propagate Redis exceptions."""
        bad_redis = MagicMock()
        bad_redis.publish.side_effect = ConnectionError("Redis down")

        with patch.object(progress_mod, "_get_redis", return_value=bad_redis):
            # Should not raise
            publish_progress("task-x", STEP_QUEUED)


# ---------------------------------------------------------------------------
# Tests — subscribe_progress
# ---------------------------------------------------------------------------


class TestSubscribeProgress:
    """Verify subscribe_progress yields events and stops on done."""

    def test_yields_events_until_done(self, fake_redis):
        """Subscriber reads stored state, then Pub/Sub events."""
        from ph_stocks_advisor.web.progress import subscribe_progress

        # Simulate: worker already published progress (stored in state key
        # AND in the pub/sub queue).
        with patch.object(progress_mod, "_get_redis", return_value=fake_redis):
            publish_progress("task-sub", STEP_FETCHING)
            publish_progress("task-sub", STEP_SAVING, done=True, verdict="BUY")

        # The state key now holds the LAST event (done=True).
        # subscribe_progress should read it and return immediately.
        with patch.object(progress_mod, "_get_redis", return_value=fake_redis):
            events = list(subscribe_progress("task-sub"))

        # Should get the stored state (done event) and stop.
        assert len(events) >= 1
        assert events[-1]["done"] is True
        assert events[-1]["verdict"] == "BUY"

    def test_catches_up_from_state_key_when_pubsub_missed(self, fake_redis):
        """If pub/sub events were missed, the state key catches up."""
        from ph_stocks_advisor.web.progress import subscribe_progress

        # Write directly to the state key (simulating worker already done).
        done_event = {"step": 5, "label": "Saving report", "done": True, "verdict": "BUY"}
        fake_redis.set("analysis:state:task-late", json.dumps(done_event))

        with patch.object(progress_mod, "_get_redis", return_value=fake_redis):
            events = list(subscribe_progress("task-late"))

        assert len(events) == 1
        assert events[0]["done"] is True


# ---------------------------------------------------------------------------
# Tests — /stream/<task_id> SSE endpoint
# ---------------------------------------------------------------------------


class TestStreamEndpoint:
    """Verify the Flask /stream/<task_id> route emits SSE events."""

    def test_stream_returns_sse_content_type(self, client, fake_redis):
        """The endpoint should set the correct MIME type."""
        # Store a done event in the state key so the stream terminates.
        done_event = {"step": 5, "label": "Done", "done": True}
        fake_redis.set("analysis:state:task-sse", json.dumps(done_event))

        with patch.object(progress_mod, "_get_redis", return_value=fake_redis):
            resp = client.get("/stream/task-sse")

        assert resp.content_type.startswith("text/event-stream")

    def test_stream_emits_data_lines(self, client, fake_redis):
        """Events should be formatted as SSE data lines."""
        # Store a done event in the state key.
        done_event = {"step": 5, "done": True, "verdict": "BUY", "label": "Saving report"}
        fake_redis.set("analysis:state:task-sse2", json.dumps(done_event))

        with patch.object(progress_mod, "_get_redis", return_value=fake_redis):
            resp = client.get("/stream/task-sse2")

        body = resp.data.decode()
        lines = [l for l in body.split("\n") if l.startswith("data:")]
        assert len(lines) >= 1

        last = json.loads(lines[-1].removeprefix("data: "))
        assert last["done"] is True
        assert last["verdict"] == "BUY"

    def test_stream_sets_no_cache_headers(self, client, fake_redis):
        done_event = {"step": 0, "done": True, "label": "Queued"}
        fake_redis.set("analysis:state:task-sse3", json.dumps(done_event))

        with patch.object(progress_mod, "_get_redis", return_value=fake_redis):
            resp = client.get("/stream/task-sse3")

        assert resp.headers.get("Cache-Control") == "no-cache"
        assert resp.headers.get("X-Accel-Buffering") == "no"


# ---------------------------------------------------------------------------
# Tests — step constants consistency
# ---------------------------------------------------------------------------


class TestStepConstants:
    """Verify that STEP_LABELS covers all defined step constants."""

    def test_all_steps_have_labels(self):
        steps = [
            STEP_QUEUED,
            STEP_VALIDATING,
            STEP_FETCHING,
            STEP_AGENTS,
            STEP_CONSOLIDATING,
            STEP_SAVING,
        ]
        for step in steps:
            assert step in STEP_LABELS, f"STEP {step} missing from STEP_LABELS"

    def test_steps_are_sequential(self):
        steps = [
            STEP_QUEUED,
            STEP_VALIDATING,
            STEP_FETCHING,
            STEP_AGENTS,
            STEP_CONSOLIDATING,
            STEP_SAVING,
        ]
        for i, step in enumerate(steps):
            assert step == i, f"Expected step {i} but got {step}"
