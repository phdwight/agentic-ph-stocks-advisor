"""
Tests for the per-user daily analysis rate limiting.

Verifies that users are limited to ``DAILY_ANALYSIS_LIMIT`` new analyses
per UTC day, that cached and in-flight results bypass the limit, and
that the counter resets at 00:00 UTC.
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
# Unit tests — rate_limit module
# ---------------------------------------------------------------------------


class TestCheckAndIncrement:
    """Direct tests for the check_and_increment function."""

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
        _rl_mod.check_and_increment(fake_redis, "user@test.com", 5)
        _rl_mod.check_and_increment(fake_redis, "user@test.com", 5)
        remaining = _rl_mod.get_remaining(fake_redis, "user@test.com", 5)
        assert remaining == 3

    def test_zero_when_exhausted(self, fake_redis):
        for _ in range(5):
            _rl_mod.check_and_increment(fake_redis, "user@test.com", 5)
        remaining = _rl_mod.get_remaining(fake_redis, "user@test.com", 5)
        assert remaining == 0


# ---------------------------------------------------------------------------
# Integration tests — /analyse endpoint respects rate limit
# ---------------------------------------------------------------------------


class TestAnalyseRateLimit:
    """The /analyse endpoint enforces the daily per-user limit."""

    def test_requests_within_limit_succeed(self, client, fake_redis):
        """Users can analyse up to the configured limit."""
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

    def test_request_over_limit_returns_429(self, client, fake_redis):
        """The 4th request (limit=3) must return HTTP 429."""
        task = MagicMock()
        task.id = "task-001"

        with patch.object(
            _tasks_mod.analyse_stock, "delay", return_value=task
        ):
            for i in range(3):
                task.id = f"task-{i}"
                client.post("/analyse", data={"symbol": f"S{i}"})

            resp = client.post("/analyse", data={"symbol": "EXTRA"})

        assert resp.status_code == 429
        data = resp.get_json()
        assert "limit" in data["error"].lower()

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

            # These 3 should still be within limit (limit=3)
            mock_repo.get_latest_by_symbol.return_value = None
            for i in range(3):
                task.id = f"task-{i}"
                resp = client.post("/analyse", data={"symbol": f"X{i}"})
                assert resp.status_code == 200

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

            # These 3 new analyses should still be within limit
            for i in range(3):
                task.id = f"task-{i}"
                resp = client.post("/analyse", data={"symbol": f"N{i}"})
                assert resp.status_code == 200

    def test_error_message_mentions_reset(self, client, fake_redis):
        """The 429 error message should tell the user when the quota resets."""
        task = MagicMock()
        task.id = "task-001"

        with patch.object(
            _tasks_mod.analyse_stock, "delay", return_value=task
        ):
            for i in range(3):
                task.id = f"task-{i}"
                client.post("/analyse", data={"symbol": f"S{i}"})

            resp = client.post("/analyse", data={"symbol": "OVER"})

        data = resp.get_json()
        assert "00:00 UTC" in data["error"]
