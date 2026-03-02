"""
Tests for the /healthz heartbeat endpoint.

Verifies that the heartbeat returns correct status and HTTP codes
depending on whether Redis and the database are reachable.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import ph_stocks_advisor.web.app as _app_mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeRedis:
    """Minimal Redis stub that supports ping and basic key ops."""

    def ping(self) -> bool:
        return True

    def get(self, key: str) -> str | None:
        return None

    def set(self, key: str, value: str, ex: int | None = None) -> None:  # noqa: A003
        pass

    def incr(self, key: str) -> int:
        return 1

    def expire(self, key: str, seconds: int) -> None:
        pass

    def delete(self, key: str) -> None:
        pass

    def scan_iter(self, pattern: str) -> list[str]:
        return []


class FailingRedis(FakeRedis):
    """Redis stub whose ping always raises."""

    def ping(self) -> bool:
        raise ConnectionError("Redis unreachable")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def _make_client(monkeypatch):
    """Factory fixture that builds a Flask test client with the given mocks."""

    def _build(*, redis_instance=None, repo_instance=None):
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

        fake_redis = redis_instance or FakeRedis()
        mock_repo = repo_instance or MagicMock()
        mock_repo.get_latest_by_symbol.return_value = None
        mock_repo.list_recent_symbols.return_value = []

        ctx = (
            patch.object(_app_mod, "get_repository", return_value=mock_repo),
            patch.object(_app_mod, "get_redis", return_value=fake_redis),
        )
        stack = None
        for cm in ctx:
            entered = cm.__enter__()  # noqa: SIM117
            if stack is None:
                stack = [cm]
            else:
                stack.append(cm)

        app = _app_mod.create_app()
        app.config["TESTING"] = True
        client = app.test_client()

        class _Client:
            def __getattr__(self, name):
                return getattr(client, name)

            def close(self):
                for cm in reversed(stack):
                    cm.__exit__(None, None, None)
                get_settings.cache_clear()

        return _Client()

    return _build


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestHealthzEndpoint:
    """Heartbeat endpoint behaviour under different dependency states."""

    def test_healthy_when_all_deps_ok(self, _make_client):
        """Returns 200 with healthy status when Redis and DB are reachable."""
        c = _make_client()
        try:
            resp = c.get("/healthz")
            data = resp.get_json()

            assert resp.status_code == 200
            assert data["status"] == "healthy"
            assert data["checks"]["redis"] == "ok"
            assert data["checks"]["database"] == "ok"
        finally:
            c.close()

    def test_unhealthy_when_redis_down(self, _make_client):
        """Returns 503 when Redis is unreachable."""
        c = _make_client(redis_instance=FailingRedis())
        try:
            resp = c.get("/healthz")
            data = resp.get_json()

            assert resp.status_code == 503
            assert data["status"] == "unhealthy"
            assert "error" in data["checks"]["redis"]
            # DB should still be ok
            assert data["checks"]["database"] == "ok"
        finally:
            c.close()

    def test_unhealthy_when_db_down(self, _make_client):
        """Returns 503 when the database is unreachable."""
        failing_repo = MagicMock()
        failing_repo.list_recent_symbols.side_effect = Exception("DB connection refused")
        failing_repo.close.return_value = None

        c = _make_client(repo_instance=failing_repo)
        try:
            resp = c.get("/healthz")
            data = resp.get_json()

            assert resp.status_code == 503
            assert data["status"] == "unhealthy"
            assert "error" in data["checks"]["database"]
            # Redis should still be ok
            assert data["checks"]["redis"] == "ok"
        finally:
            c.close()

    def test_unhealthy_when_all_deps_down(self, _make_client):
        """Returns 503 when both Redis and DB are down."""
        failing_repo = MagicMock()
        failing_repo.list_recent_symbols.side_effect = Exception("DB down")
        failing_repo.close.return_value = None

        c = _make_client(redis_instance=FailingRedis(), repo_instance=failing_repo)
        try:
            resp = c.get("/healthz")
            data = resp.get_json()

            assert resp.status_code == 503
            assert data["status"] == "unhealthy"
            assert "error" in data["checks"]["redis"]
            assert "error" in data["checks"]["database"]
        finally:
            c.close()

    def test_healthz_does_not_require_auth(self, _make_client):
        """The healthz endpoint must be accessible without login."""
        c = _make_client()
        try:
            resp = c.get("/healthz")
            # Should NOT redirect to login (302) — should respond directly
            assert resp.status_code == 200
        finally:
            c.close()
