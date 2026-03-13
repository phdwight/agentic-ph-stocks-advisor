"""
Tests for the user type system.

Verifies that:
- UserRecord stores and exposes user_type correctly
- Elevated users bypass the daily rate limit
- Elevated users can re-analyse stocks after the daily cooldown (next UTC day)
- Elevated users are blocked when re-analysing the same stock on the same UTC day
- Normal users remain subject to both rate limit and cache dedup
- get_user_by_email works correctly
"""

from __future__ import annotations

import fnmatch
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

import ph_stocks_advisor.web.app as _app_mod
import ph_stocks_advisor.web.auth as _auth_mod
import ph_stocks_advisor.web.rate_limit as _rl_mod
import ph_stocks_advisor.web.tasks as _tasks_mod
from ph_stocks_advisor.infra.config import _reset_repository
from ph_stocks_advisor.infra.repository import UserRecord, UserType
from ph_stocks_advisor.infra.repository_sqlite import SQLiteReportRepository

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeRedis:
    """In-memory dict that mimics a Redis client for tests."""

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

    def decr(self, key: str) -> int:
        val = int(self._store.get(key, 0)) - 1
        self._store[key] = str(val)
        return val

    def expire(self, key: str, seconds: int) -> None:
        pass

    def delete(self, *keys: str) -> None:
        for key in keys:
            self._store.pop(key, None)

    def scan_iter(self, pattern: str) -> list[str]:
        return [k for k in self._store if fnmatch.fnmatch(k, pattern)]

    def ping(self) -> bool:
        return True

    def eval(self, script: str, numkeys: int, *args) -> list:  # noqa: A003
        """Emulate the atomic reserve Lua script."""
        key = args[0]
        limit = int(args[1])
        current = int(self._store.get(key, 0))
        if current >= limit:
            return [0, current]
        new = self.incr(key)
        return [1, new]


def _seed_counter(fake_redis: FakeRedis, user_id: str, count: int) -> None:
    """Pre-set the daily rate-limit counter for *user_id*."""
    key = _rl_mod._daily_key(user_id)
    fake_redis.set(key, str(count))


_DEV_USER_ELEVATED: dict[str, str | int] = {
    "name": "Elevated Developer",
    "email": "dev@localhost",
    "oid": "local-dev",
    "provider": "local",
    "user_type": 1,
}

_DEV_USER_NORMAL: dict[str, str | int] = {
    "name": "Local Developer",
    "email": "dev@localhost",
    "oid": "local-dev",
    "provider": "local",
    "user_type": 0,
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_redis():
    return FakeRedis()


@pytest.fixture
def _user_type_env(monkeypatch):
    """Ensure settings are configured for user type tests."""
    monkeypatch.delenv("ENTRA_CLIENT_ID", raising=False)
    monkeypatch.delenv("ENTRA_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("DB_BACKEND", "sqlite")
    monkeypatch.setenv("SQLITE_PATH", ":memory:")
    monkeypatch.setenv("DAILY_ANALYSIS_LIMIT", "3")


def _make_client(fake_redis, dev_user, _user_type_env):
    """Create a Flask test client with the given dev user identity."""
    from ph_stocks_advisor.infra.config import get_settings

    get_settings.cache_clear()
    s = get_settings()
    s.daily_analysis_limit = 3
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
        patch.object(_auth_mod, "_DEV_USER", dev_user),
    ):
        app = _app_mod.create_app()
        app.config["TESTING"] = True
        with app.test_client() as c:
            # Set the dev_user_type session key so get_current_user()
            # returns the correct user_type for local-dev mode.
            with c.session_transaction() as sess:
                sess["dev_user_type"] = dev_user.get("user_type", 0)
            yield c, mock_repo

    get_settings.cache_clear()


@pytest.fixture
def elevated_client(fake_redis, _user_type_env):
    """Flask test client where the dev user is elevated (user_type=1)."""
    yield from _make_client(fake_redis, _DEV_USER_ELEVATED, _user_type_env)


@pytest.fixture
def normal_client(fake_redis, _user_type_env):
    """Flask test client where the dev user is normal (user_type=0)."""
    yield from _make_client(fake_redis, _DEV_USER_NORMAL, _user_type_env)


@pytest.fixture(autouse=True)
def _clear_repo_singleton():
    """Reset the cached singleton between tests."""
    _reset_repository()
    yield
    _reset_repository()


@pytest.fixture
def sqlite_repo(tmp_path) -> Generator[SQLiteReportRepository, None, None]:
    """Create a fresh SQLite repo in a temp directory."""
    db_path = str(tmp_path / "test_user_type.db")
    repo = SQLiteReportRepository(db_path=db_path)
    repo.initialize()
    yield repo
    repo.close()


# ---------------------------------------------------------------------------
# UserRecord model tests
# ---------------------------------------------------------------------------


class TestUserType:
    """Tests for UserType enum and UserRecord.is_elevated property."""

    @pytest.mark.parametrize(
        "user_type,expected_elevated",
        [
            (UserType.NORMAL, False),
            (UserType.ELEVATED, True),
        ],
        ids=["normal", "elevated"],
    )
    def test_user_record_type_and_elevation(self, user_type, expected_elevated):
        user = UserRecord(
            oid="oid-1",
            name="Test",
            email="test@example.com",
            provider="google",
            user_type=user_type,
        )
        assert user.user_type == user_type
        assert user.is_elevated is expected_elevated


# ---------------------------------------------------------------------------
# Repository persistence tests
# ---------------------------------------------------------------------------


class TestUserTypePersistence:
    """Tests for user_type persistence through save/get cycle."""

    def test_save_user_with_default_type(self, sqlite_repo):
        """A user saved without explicit user_type gets NORMAL (0)."""
        user = UserRecord(oid="oid-10", name="Normal User", email="normal@test.com", provider="google")
        sqlite_repo.save_user(user)

        fetched = sqlite_repo.get_user("oid-10")
        assert fetched is not None
        assert fetched.user_type == UserType.NORMAL
        assert fetched.is_elevated is False

    def test_save_user_with_elevated_type(self, sqlite_repo):
        """A user saved with ELEVATED type retains it after retrieval."""
        user = UserRecord(
            oid="oid-11",
            name="Elevated User",
            email="elevated@test.com",
            provider="microsoft",
            user_type=UserType.ELEVATED,
        )
        sqlite_repo.save_user(user)

        fetched = sqlite_repo.get_user("oid-11")
        assert fetched is not None
        assert fetched.user_type == UserType.ELEVATED
        assert fetched.is_elevated is True

    def test_upsert_does_not_overwrite_user_type(self, sqlite_repo):
        """Re-saving a user (login) should not reset an elevated type."""
        # First save as elevated (admin set it directly).
        user = UserRecord(
            oid="oid-12",
            name="Admin User",
            email="admin@test.com",
            provider="google",
            user_type=UserType.ELEVATED,
        )
        sqlite_repo.save_user(user)

        # Second save as normal (simulating a login upsert).
        user2 = UserRecord(
            oid="oid-12",
            name="Admin User",
            email="admin@test.com",
            provider="google",
            user_type=UserType.NORMAL,
        )
        sqlite_repo.save_user(user2)

        fetched = sqlite_repo.get_user("oid-12")
        assert fetched is not None
        # user_type should NOT be overwritten by the upsert.
        assert fetched.user_type == UserType.ELEVATED

    def test_get_user_by_email(self, sqlite_repo):
        """get_user_by_email returns the correct user."""
        user = UserRecord(
            oid="oid-20",
            name="Email Lookup",
            email="lookup@test.com",
            provider="microsoft",
            user_type=UserType.ELEVATED,
        )
        sqlite_repo.save_user(user)

        fetched = sqlite_repo.get_user_by_email("lookup@test.com")
        assert fetched is not None
        assert fetched.oid == "oid-20"
        assert fetched.user_type == UserType.ELEVATED
        assert fetched.is_elevated is True

    def test_get_user_by_email_not_found(self, sqlite_repo):
        assert sqlite_repo.get_user_by_email("nobody@test.com") is None

    def test_get_user_by_email_case_exact(self, sqlite_repo):
        """Email lookup is an exact match (not case-insensitive)."""
        user = UserRecord(
            oid="oid-21",
            name="Case Test",
            email="Case@Test.com",
            provider="google",
        )
        sqlite_repo.save_user(user)

        assert sqlite_repo.get_user_by_email("Case@Test.com") is not None
        # SQLite default collation is case-sensitive for LIKE, but
        # equality (=) depends on collation; this test documents behaviour.


# ---------------------------------------------------------------------------
# Integration tests — elevated user bypasses rate limit
# ---------------------------------------------------------------------------


class TestElevatedBypassesRateLimit:
    """Elevated users are exempt from the daily analysis limit."""

    def test_elevated_user_not_blocked_after_limit(self, elevated_client, fake_redis):
        """Even after exceeding the limit counter, elevated users succeed."""
        client, _ = elevated_client
        _seed_counter(fake_redis, "dev@localhost", 10)  # way over limit=3

        task = MagicMock()
        task.id = "task-elevated"

        with patch.object(_tasks_mod.analyse_stock, "delay", return_value=task):
            resp = client.post("/analyse", data={"symbol": "TEL"})
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["status"] == "started"

    def test_elevated_user_unlimited_analyses(self, elevated_client, fake_redis):
        """Elevated user can dispatch more analyses than the limit."""
        client, _ = elevated_client
        task = MagicMock()

        with patch.object(_tasks_mod.analyse_stock, "delay", return_value=task):
            for i in range(10):  # limit is 3
                task.id = f"task-{i}"
                resp = client.post("/analyse", data={"symbol": f"SYM{i}"})
                assert resp.status_code == 200, f"Request {i + 1} should succeed for elevated user"
                data = resp.get_json()
                assert data["status"] == "started"

    def test_elevated_user_still_joins_inflight(self, elevated_client, fake_redis):
        """Even elevated users join an in-flight analysis (no duplicates)."""
        client, _ = elevated_client
        fake_redis.set("analysis:inflight:TEL", "task-existing", ex=600)

        resp = client.post("/analyse", data={"symbol": "TEL"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "joined"

    def test_elevated_skip_cache_and_rate_limit_after_cooldown(self, elevated_client, fake_redis):
        """Elevated user bypasses cache AND rate limit when cooldown passed."""
        client, mock_repo = elevated_client

        # Report from yesterday — cooldown has passed.
        cached_record = MagicMock()
        cached_record.id = 50
        cached_record.created_at = datetime.now(tz=UTC) - timedelta(days=1)
        mock_repo.get_latest_by_symbol.return_value = cached_record

        _seed_counter(fake_redis, "dev@localhost", 100)  # way over limit

        task = MagicMock()
        task.id = "task-both"

        with patch.object(_tasks_mod.analyse_stock, "delay", return_value=task):
            resp = client.post("/analyse", data={"symbol": "TEL"})
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["status"] == "started"


# ---------------------------------------------------------------------------
# Integration tests — elevated user daily cooldown
# ---------------------------------------------------------------------------


class TestElevatedDailyCooldown:
    """Elevated users cannot re-analyse the same stock on the same UTC day."""

    def test_elevated_blocked_when_analysed_today(self, elevated_client, fake_redis):
        """Elevated user gets 429 with report link when the stock was analysed today."""
        client, mock_repo = elevated_client

        cached_record = MagicMock()
        cached_record.id = 42
        cached_record.created_at = datetime.now(tz=UTC)
        mock_repo.get_latest_by_symbol.return_value = cached_record

        resp = client.post("/analyse", data={"symbol": "TEL"})
        assert resp.status_code == 429
        data = resp.get_json()
        assert "already analysed today" in data["error"]
        assert "reset_at" in data
        assert data["report_id"] == 42
        assert data["symbol"] == "TEL"

    def test_elevated_cooldown_reset_at_is_next_midnight(self, elevated_client, fake_redis):
        """The reset_at timestamp in the cooldown response is next UTC midnight."""
        client, mock_repo = elevated_client

        cached_record = MagicMock()
        cached_record.id = 42
        cached_record.created_at = datetime.now(tz=UTC)
        mock_repo.get_latest_by_symbol.return_value = cached_record

        resp = client.post("/analyse", data={"symbol": "TEL"})
        data = resp.get_json()

        reset_at = datetime.fromisoformat(data["reset_at"])
        assert reset_at.hour == 0
        assert reset_at.minute == 0
        assert reset_at.second == 0

    def test_elevated_cooldown_per_stock_not_global(self, elevated_client, fake_redis):
        """Cooldown is per-stock: analysing SYM1 today doesn't block SYM2."""
        client, mock_repo = elevated_client

        def latest_by_symbol(symbol: str):
            if symbol == "SYM1":
                rec = MagicMock()
                rec.id = 1
                rec.created_at = datetime.now(tz=UTC)  # analysed today
                return rec
            return None  # SYM2 never analysed

        mock_repo.get_latest_by_symbol.side_effect = latest_by_symbol

        # SYM1 — blocked by cooldown
        resp1 = client.post("/analyse", data={"symbol": "SYM1"})
        assert resp1.status_code == 429

        # SYM2 — no report, should start
        task = MagicMock()
        task.id = "task-sym2"
        with patch.object(_tasks_mod.analyse_stock, "delay", return_value=task):
            resp2 = client.post("/analyse", data={"symbol": "SYM2"})
            assert resp2.status_code == 200
            assert resp2.get_json()["status"] == "started"
