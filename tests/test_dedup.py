"""
Tests for the concurrent analysis deduplication logic.

Verifies that when multiple users request analysis for the same stock
simultaneously, only one Celery task is dispatched and subsequent
requests join the in-flight task instead of creating duplicates.
"""

from __future__ import annotations

import fnmatch
from unittest.mock import MagicMock, patch

import pytest

# Import the modules eagerly so ``patch.object`` can find attributes.
import ph_stocks_advisor.web.app as _app_mod  # noqa: E402
import ph_stocks_advisor.web.tasks as _tasks_mod  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeRedis:
    """In-memory dict that mimics a Redis client."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self._store.get(key)

    def set(self, key: str, value: str, ex: int | None = None) -> None:  # noqa: A003
        self._store[key] = value

    def delete(self, key: str) -> None:
        self._store.pop(key, None)

    def scan_iter(self, pattern: str) -> list[str]:
        return [k for k in self._store if fnmatch.fnmatch(k, pattern)]


@pytest.fixture
def fake_redis():
    return FakeRedis()


@pytest.fixture
def client(fake_redis):
    """Flask test client with all external deps mocked."""
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


# ---------------------------------------------------------------------------
# Tests — analyse dedup
# ---------------------------------------------------------------------------


class TestAnalyseDedup:
    """Concurrent analysis deduplication via Redis inflight lock."""

    def test_first_request_dispatches_new_task(self, client, fake_redis):
        """First request for a symbol should dispatch a new Celery task."""
        task_result = MagicMock()
        task_result.id = "task-abc-123"

        with patch.object(
            _tasks_mod.analyse_stock, "delay", return_value=task_result
        ) as mock_delay:
            resp = client.post("/analyse", data={"symbol": "TEL"})

        data = resp.get_json()
        assert resp.status_code == 200
        assert data["status"] == "started"
        assert data["task_id"] == "task-abc-123"
        mock_delay.assert_called_once_with("TEL")
        # Lock should be stored in Redis
        assert fake_redis.get("analysis:inflight:TEL") == "task-abc-123"

    def test_second_request_joins_inflight_task(self, client, fake_redis):
        """Second concurrent request should reuse the in-flight task."""
        fake_redis.set("analysis:inflight:TEL", "task-abc-123", ex=600)

        with patch.object(
            _tasks_mod.analyse_stock, "delay"
        ) as mock_delay:
            resp = client.post("/analyse", data={"symbol": "TEL"})

        data = resp.get_json()
        assert resp.status_code == 200
        assert data["status"] == "joined"
        assert data["task_id"] == "task-abc-123"
        mock_delay.assert_not_called()

    def test_different_symbols_dispatch_separately(self, client, fake_redis):
        """Different symbols should each get their own task."""
        fake_redis.set("analysis:inflight:TEL", "task-tel-001", ex=600)

        task_sm = MagicMock()
        task_sm.id = "task-sm-001"

        with patch.object(
            _tasks_mod.analyse_stock, "delay", return_value=task_sm
        ) as mock_delay:
            resp = client.post("/analyse", data={"symbol": "SM"})

        data = resp.get_json()
        assert data["status"] == "started"
        assert data["task_id"] == "task-sm-001"
        mock_delay.assert_called_once_with("SM")

    def test_cancel_clears_inflight_lock(self, client, fake_redis):
        """Cancelling a task should remove its inflight lock."""
        fake_redis.set("analysis:inflight:TEL", "task-abc-123", ex=600)

        with patch.object(_tasks_mod, "celery_app"):
            resp = client.post("/cancel/task-abc-123")

        assert resp.status_code == 200
        assert fake_redis.get("analysis:inflight:TEL") is None


# ---------------------------------------------------------------------------
# Tests — worker lock cleanup
# ---------------------------------------------------------------------------


class TestInflightLockCleanup:
    """Verify the worker clears the inflight lock after task completion."""

    def test_clear_inflight_lock_deletes_key(self, fake_redis):
        """_clear_inflight_lock should remove the Redis key for the symbol."""
        fake_redis.set("analysis:inflight:SM", "task-sm-001", ex=600)

        with patch("redis.from_url", return_value=fake_redis):
            _tasks_mod._clear_inflight_lock("SM")

        assert fake_redis.get("analysis:inflight:SM") is None

    def test_clear_inflight_lock_handles_redis_failure(self):
        """If Redis is down, _clear_inflight_lock should not raise."""
        with patch("redis.from_url", side_effect=Exception("Redis down")):
            # Should not raise
            _tasks_mod._clear_inflight_lock("TEL")
