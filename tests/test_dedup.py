"""
Tests for the concurrent analysis deduplication logic.

Verifies that when multiple users request analysis for the same stock
simultaneously, only one Celery task is dispatched and subsequent
requests join the in-flight task instead of creating duplicates.
"""

from __future__ import annotations

import fnmatch
from typing import cast
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

    def set(self, key: str, value: str, ex: int | None = None, nx: bool = False) -> bool | None:  # noqa: A003
        """Mimic redis-py: with nx=True, only set if absent (None if it exists)."""
        if nx and key in self._store:
            return None
        self._store[key] = value
        return True

    def incr(self, key: str) -> int:
        val = int(self._store.get(key, 0)) + 1
        self._store[key] = str(val)
        return val

    def expire(self, key: str, seconds: int) -> None:
        pass  # no-op for tests

    def delete(self, *keys: str) -> None:
        for key in keys:
            self._store.pop(key, None)

    def scan_iter(self, pattern: str) -> list[str]:
        return [k for k in self._store if fnmatch.fnmatch(k, pattern)]

    def decr(self, key: str) -> int:
        val = int(self._store.get(key, 0)) - 1
        self._store[key] = str(val)
        return val

    def eval(self, script: str, numkeys: int, *args) -> list:  # noqa: A003
        """Emulate the atomic reserve Lua script."""
        key = args[0]
        limit = int(args[1])
        current = int(self._store.get(key, 0))
        if current >= limit:
            return [0, current]
        new = self.incr(key)
        return [1, new]


@pytest.fixture
def fake_redis():
    return FakeRedis()


@pytest.fixture
def client(fake_redis, monkeypatch):
    """Flask test client with all external deps mocked."""
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
        patch.object(_app_mod, "get_redis", return_value=fake_redis),
    ):
        app = _app_mod.create_app()
        app.config["TESTING"] = True
        with app.test_client() as c:
            yield c

    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Tests — analyse dedup
# ---------------------------------------------------------------------------


class TestAnalyseDedup:
    """Concurrent analysis deduplication via Redis inflight lock."""

    def test_first_request_dispatches_new_task(self, client, fake_redis):
        """First request for a symbol should dispatch a new Celery task."""
        with patch.object(_tasks_mod.analyse_stock, "apply_async") as mock_dispatch:
            resp = client.post("/analyse", data={"symbol": "TEL"})

        data = resp.get_json()
        assert resp.status_code == 200
        assert data["status"] == "started"
        task_id = data["task_id"]
        # Dispatched under the SAME pre-generated id stored in the lock,
        # so joiners always stream the right task.
        mock_dispatch.assert_called_once_with(args=["TEL"], kwargs={"user_id": "dev@localhost"}, task_id=task_id)
        assert fake_redis.get("analysis:inflight:TEL") == task_id
        # Reverse mapping for O(1) cancel should also be stored
        assert fake_redis.get(f"analysis:task:{task_id}") == "TEL"

    def test_second_request_joins_inflight_task(self, client, fake_redis):
        """Second concurrent request should reuse the in-flight task."""
        fake_redis.set("analysis:inflight:TEL", "task-abc-123", ex=600)

        with patch.object(_tasks_mod.analyse_stock, "apply_async") as mock_dispatch:
            resp = client.post("/analyse", data={"symbol": "TEL"})

        data = resp.get_json()
        assert resp.status_code == 200
        assert data["status"] == "joined"
        assert data["task_id"] == "task-abc-123"
        mock_dispatch.assert_not_called()

    def test_concurrent_claims_yield_one_dispatch(self, client, fake_redis):
        """The SET NX claim admits exactly one dispatch for the same symbol.

        Sequential requests through the test client share the same lock
        state, so the second request exercises the exact code path a truly
        concurrent loser takes: failed claim -> join the stored task id.
        """
        with patch.object(_tasks_mod.analyse_stock, "apply_async") as mock_dispatch:
            first = client.post("/analyse", data={"symbol": "BDO"}).get_json()
            second = client.post("/analyse", data={"symbol": "BDO"}).get_json()

        assert first["status"] == "started"
        assert second["status"] == "joined"
        assert second["task_id"] == first["task_id"]  # same run, same stream
        assert mock_dispatch.call_count == 1  # exactly one execution

    def test_different_symbols_dispatch_separately(self, client, fake_redis):
        """Different symbols should each get their own task."""
        fake_redis.set("analysis:inflight:TEL", "task-tel-001", ex=600)

        with patch.object(_tasks_mod.analyse_stock, "apply_async") as mock_dispatch:
            resp = client.post("/analyse", data={"symbol": "SM"})

        data = resp.get_json()
        assert data["status"] == "started"
        assert data["task_id"] != "task-tel-001"
        assert mock_dispatch.call_count == 1

    def test_dispatch_failure_releases_claim(self, client, fake_redis):
        """If Celery dispatch raises, the inflight claim must be released."""
        with (
            patch.object(_tasks_mod.analyse_stock, "apply_async", side_effect=RuntimeError("broker down")),
            pytest.raises(RuntimeError),
        ):
            client.post("/analyse", data={"symbol": "TEL"})

        assert fake_redis.get("analysis:inflight:TEL") is None  # others can retry

    def test_vanished_lock_serves_latest_report(self, client, fake_redis):
        """Claim lost + lock gone (run just finished) -> serve the saved report."""
        report = MagicMock()
        report.id = 77
        report.created_at = None  # forces the "no fresh report" path first

        real_set = fake_redis.set

        def racing_set(key, value, ex=None, nx=False):
            if nx and key == "analysis:inflight:TEL":
                return None  # claim always loses, but no lock value exists
            return real_set(key, value, ex=ex, nx=nx)

        fake_redis.set = racing_set
        mock_repo = cast(MagicMock, _app_mod.get_repository())
        mock_repo.get_latest_by_symbol.return_value = report

        resp = client.post("/analyse", data={"symbol": "TEL"})
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["status"] == "cached"
        assert data["report_id"] == 77

    def test_cancel_clears_inflight_lock(self, client, fake_redis):
        """Cancelling a task should remove its inflight lock via reverse mapping."""
        fake_redis.set("analysis:inflight:TEL", "task-abc-123", ex=600)
        fake_redis.set("analysis:task:task-abc-123", "TEL", ex=600)

        with patch.object(_tasks_mod, "celery_app"):
            resp = client.post("/cancel/task-abc-123")

        assert resp.status_code == 200
        assert fake_redis.get("analysis:inflight:TEL") is None
        assert fake_redis.get("analysis:task:task-abc-123") is None


# ---------------------------------------------------------------------------
# Tests — worker lock cleanup
# ---------------------------------------------------------------------------


class TestInflightLockCleanup:
    """Verify the worker clears the inflight lock after task completion."""

    def test_clear_inflight_lock_deletes_key(self, fake_redis):
        """_clear_inflight_lock should remove both the symbol key and reverse mapping."""
        fake_redis.set("analysis:inflight:SM", "task-sm-001", ex=600)
        fake_redis.set("analysis:task:task-sm-001", "SM", ex=600)

        with patch("ph_stocks_advisor.infra.config.get_redis", return_value=fake_redis):
            _tasks_mod._clear_inflight_lock("SM", task_id="task-sm-001")

        assert fake_redis.get("analysis:inflight:SM") is None
        assert fake_redis.get("analysis:task:task-sm-001") is None

    def test_clear_inflight_lock_without_task_id(self, fake_redis):
        """_clear_inflight_lock without task_id should only remove symbol key."""
        fake_redis.set("analysis:inflight:SM", "task-sm-001", ex=600)

        with patch("ph_stocks_advisor.infra.config.get_redis", return_value=fake_redis):
            _tasks_mod._clear_inflight_lock("SM")

        assert fake_redis.get("analysis:inflight:SM") is None

    def test_clear_inflight_lock_handles_redis_failure(self):
        """If Redis is down, _clear_inflight_lock should not raise."""
        with patch("ph_stocks_advisor.infra.config.get_redis", side_effect=Exception("Redis down")):
            # Should not raise
            _tasks_mod._clear_inflight_lock("TEL", task_id="task-tel-001")
