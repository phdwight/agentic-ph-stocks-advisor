"""
Tests for the per-user daily analysis rate limiting.

Verifies that users are limited to ``DAILY_ANALYSIS_LIMIT`` new analyses
per UTC day, that cached and in-flight results bypass the limit, that
the counter resets at 00:00 UTC, and that only **successful first-time
analyses** consume quota (failed analyses do not count).
"""

from __future__ import annotations

import fnmatch
from unittest.mock import MagicMock, patch

import pytest

import ph_stocks_advisor.web.app as _app_mod
import ph_stocks_advisor.web.rate_limit as _rl_mod
import ph_stocks_advisor.web.tasks as _tasks_mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeRedis:
    """In-memory dict that mimics a Redis client for rate-limit tests."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self._store.get(key)

    def set(self, key: str, value: str, ex: int | None = None) -> None:  # noqa: A003
        self._store[key] = value

    def incr(self, key: str) -> int:
        val = int(self._store.get(key, 0)) + 1
        self._store[key] = str(val)
        return val

    def expire(self, key: str, seconds: int) -> None:
        pass  # no-op for tests

    def delete(self, key: str) -> None:
        self._store.pop(key, None)

    def scan_iter(self, pattern: str) -> list[str]:
        return [k for k in self._store if fnmatch.fnmatch(k, pattern)]


def _seed_counter(fake_redis: FakeRedis, user_id: str, count: int) -> None:
    """Pre-set the daily rate-limit counter for *user_id*.

    Simulates *count* successful analyses having already occurred today.
    """
    key = _rl_mod._daily_key(user_id)
    fake_redis.set(key, str(count))


@pytest.fixture
def fake_redis():
    return FakeRedis()


@pytest.fixture
def _rate_limit_env(monkeypatch):
    """Ensure settings are configured for rate-limit tests."""
    monkeypatch.delenv("ENTRA_CLIENT_ID", raising=False)
    monkeypatch.delenv("ENTRA_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("DB_BACKEND", "sqlite")
    monkeypatch.setenv("SQLITE_PATH", ":memory:")
    monkeypatch.setenv("DAILY_ANALYSIS_LIMIT", "3")


@pytest.fixture
def client(fake_redis, _rate_limit_env):
    """Flask test client with external deps mocked and limit set to 3."""
    from ph_stocks_advisor.infra.config import get_settings

    get_settings.cache_clear()
    s = get_settings()
    s.daily_analysis_limit = 3
    # Ensure auth is disabled so login_required passes through.
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
# Unit tests — check_limit (read-only gate)
# ---------------------------------------------------------------------------


class TestCheckLimit:
    """Direct tests for the read-only check_limit function."""

    def test_allowed_when_no_usage(self, fake_redis):
        allowed, count = _rl_mod.check_limit(fake_redis, "user@test.com", 5)
        assert allowed is True
        assert count == 0

    def test_allowed_when_under_limit(self, fake_redis):
        _seed_counter(fake_redis, "user@test.com", 3)
        allowed, count = _rl_mod.check_limit(fake_redis, "user@test.com", 5)
        assert allowed is True
        assert count == 3

    def test_blocked_when_at_limit(self, fake_redis):
        _seed_counter(fake_redis, "user@test.com", 5)
        allowed, count = _rl_mod.check_limit(fake_redis, "user@test.com", 5)
        assert allowed is False
        assert count == 5

    def test_blocked_when_over_limit(self, fake_redis):
        _seed_counter(fake_redis, "user@test.com", 7)
        allowed, count = _rl_mod.check_limit(fake_redis, "user@test.com", 5)
        assert allowed is False
        assert count == 7

    def test_does_not_increment(self, fake_redis):
        """check_limit must never change the counter."""
        _rl_mod.check_limit(fake_redis, "user@test.com", 5)
        _rl_mod.check_limit(fake_redis, "user@test.com", 5)
        _rl_mod.check_limit(fake_redis, "user@test.com", 5)
        remaining = _rl_mod.get_remaining(fake_redis, "user@test.com", 5)
        assert remaining == 5  # untouched

    def test_different_users_separate(self, fake_redis):
        _seed_counter(fake_redis, "alice@test.com", 3)
        allowed_a, _ = _rl_mod.check_limit(fake_redis, "alice@test.com", 3)
        allowed_b, _ = _rl_mod.check_limit(fake_redis, "bob@test.com", 3)
        assert allowed_a is False
        assert allowed_b is True


# ---------------------------------------------------------------------------
# Unit tests — increment (called only on success)
# ---------------------------------------------------------------------------


class TestIncrement:
    """Direct tests for the increment function."""

    def test_first_increment_returns_one(self, fake_redis):
        new_count = _rl_mod.increment(fake_redis, "user@test.com")
        assert new_count == 1

    def test_successive_increments(self, fake_redis):
        for expected in range(1, 4):
            assert _rl_mod.increment(fake_redis, "user@test.com") == expected

    def test_remaining_reflects_increments(self, fake_redis):
        _rl_mod.increment(fake_redis, "user@test.com")
        _rl_mod.increment(fake_redis, "user@test.com")
        assert _rl_mod.get_remaining(fake_redis, "user@test.com", 5) == 3


# ---------------------------------------------------------------------------
# Unit tests — legacy check_and_increment (kept for backward compat)
# ---------------------------------------------------------------------------


class TestCheckAndIncrement:
    """Direct tests for the legacy check_and_increment function."""

    def test_first_request_allowed(self, fake_redis):
        allowed, count = _rl_mod.check_and_increment(fake_redis, "user@test.com", 5)
        assert allowed is True
        assert count == 1

    def test_increments_correctly(self, fake_redis):
        for i in range(1, 4):
            allowed, count = _rl_mod.check_and_increment(
                fake_redis, "user@test.com", 5
            )
            assert allowed is True
            assert count == i

    def test_blocks_at_limit(self, fake_redis):
        for _ in range(5):
            _rl_mod.check_and_increment(fake_redis, "user@test.com", 5)

        allowed, count = _rl_mod.check_and_increment(
            fake_redis, "user@test.com", 5
        )
        assert allowed is False
        assert count == 5

    def test_different_users_have_separate_counters(self, fake_redis):
        for _ in range(3):
            _rl_mod.check_and_increment(fake_redis, "alice@test.com", 3)

        allowed, _ = _rl_mod.check_and_increment(fake_redis, "alice@test.com", 3)
        assert allowed is False

        allowed, count = _rl_mod.check_and_increment(
            fake_redis, "bob@test.com", 3
        )
        assert allowed is True
        assert count == 1


class TestGetRemaining:
    """Tests for the get_remaining helper."""

    def test_full_quota_when_no_usage(self, fake_redis):
        remaining = _rl_mod.get_remaining(fake_redis, "user@test.com", 5)
        assert remaining == 5

    def test_decreases_with_usage(self, fake_redis):
        _rl_mod.increment(fake_redis, "user@test.com")
        _rl_mod.increment(fake_redis, "user@test.com")
        remaining = _rl_mod.get_remaining(fake_redis, "user@test.com", 5)
        assert remaining == 3

    def test_zero_when_exhausted(self, fake_redis):
        for _ in range(5):
            _rl_mod.increment(fake_redis, "user@test.com")
        remaining = _rl_mod.get_remaining(fake_redis, "user@test.com", 5)
        assert remaining == 0


# ---------------------------------------------------------------------------
# Integration tests — /analyse endpoint respects rate limit
# ---------------------------------------------------------------------------


class TestAnalyseRateLimit:
    """The /analyse endpoint enforces the daily per-user limit.

    Because the counter is only incremented on *successful* task
    completion (inside the Celery worker), the endpoint uses a
    read-only ``check_limit`` gate.  To simulate a user who has
    already exhausted their quota we pre-seed the Redis counter.
    """

    def test_requests_succeed_when_quota_available(self, client, fake_redis):
        """Submissions are accepted when counter is below limit."""
        task = MagicMock()
        task.id = "task-001"

        with patch.object(
            _tasks_mod.analyse_stock, "delay", return_value=task
        ):
            for i in range(3):
                task.id = f"task-{i}"
                resp = client.post("/analyse", data={"symbol": f"SYM{i}"})
                assert resp.status_code == 200, (
                    f"Request {i + 1} should succeed"
                )

    def test_request_blocked_when_quota_exhausted(self, client, fake_redis):
        """When the counter already equals the limit, HTTP 429 is returned."""
        _seed_counter(fake_redis, "dev@localhost", 3)  # limit is 3

        resp = client.post("/analyse", data={"symbol": "EXTRA"})
        assert resp.status_code == 429
        data = resp.get_json()
        assert "limit" in data["error"].lower()

    def test_submission_does_not_increment_counter(self, client, fake_redis):
        """Dispatching a task must NOT consume quota (increment happens in worker)."""
        task = MagicMock()
        task.id = "task-001"

        with patch.object(
            _tasks_mod.analyse_stock, "delay", return_value=task
        ):
            client.post("/analyse", data={"symbol": "ABC"})

        remaining = _rl_mod.get_remaining(fake_redis, "dev@localhost", 3)
        assert remaining == 3  # counter unchanged

    def test_cached_report_does_not_count(self, client, fake_redis):
        """Serving a cached report should not consume a rate-limit slot."""
        from datetime import UTC, datetime

        cached_record = MagicMock()
        cached_record.id = 42
        cached_record.created_at = datetime.now(tz=UTC)

        mock_repo = MagicMock()
        mock_repo.get_latest_by_symbol.return_value = cached_record
        mock_repo.list_recent_symbols.return_value = []

        task = MagicMock()
        task.id = "task-new"

        with (
            patch.object(_app_mod, "get_repository", return_value=mock_repo),
            patch.object(
                _tasks_mod.analyse_stock, "delay", return_value=task
            ),
        ):
            # Cached request — should not count
            resp = client.post("/analyse", data={"symbol": "TEL"})
            assert resp.status_code == 200
            assert resp.get_json()["status"] == "cached"

        remaining = _rl_mod.get_remaining(fake_redis, "dev@localhost", 3)
        assert remaining == 3

    def test_joined_inflight_does_not_count(self, client, fake_redis):
        """Joining an in-flight analysis should not consume a slot."""
        fake_redis.set("analysis:inflight:TEL", "task-existing", ex=600)

        task = MagicMock()
        task.id = "task-new"

        with patch.object(
            _tasks_mod.analyse_stock, "delay", return_value=task
        ):
            # Join — should not count
            resp = client.post("/analyse", data={"symbol": "TEL"})
            assert resp.status_code == 200
            assert resp.get_json()["status"] == "joined"

        remaining = _rl_mod.get_remaining(fake_redis, "dev@localhost", 3)
        assert remaining == 3

    def test_error_message_mentions_reset(self, client, fake_redis):
        """The 429 response includes reset_at ISO timestamp."""
        _seed_counter(fake_redis, "dev@localhost", 3)

        resp = client.post("/analyse", data={"symbol": "OVER"})

        data = resp.get_json()
        assert "quota resets" in data["error"]
        assert "reset_at" in data
        from datetime import datetime
        reset_dt = datetime.fromisoformat(data["reset_at"])
        assert reset_dt.hour == 0 and reset_dt.minute == 0

    def test_user_id_passed_to_celery_task(self, client, fake_redis):
        """The /analyse endpoint sends user_id to the Celery task."""
        task = MagicMock()
        task.id = "task-001"

        with patch.object(
            _tasks_mod.analyse_stock, "delay", return_value=task
        ) as mock_delay:
            client.post("/analyse", data={"symbol": "ABC"})

        mock_delay.assert_called_once_with("ABC", user_id="dev@localhost")
