"""
Tests for auto-refreshing stale reports when clicked by a logged-in user.

Behaviour under test:
- When a logged-in user clicks a stale report and conditions allow a fresh run
  (market closed, quota available or elevated user), a new analysis is
  dispatched (or an in-flight run is joined) with ``stale_refresh=True`` rather
  than serving the stale report, and the UI surfaces a generation notice +
  live tracker entry.
- When conditions do NOT allow generating an updated report (market is open,
  or a normal user has exhausted their daily analysis quota), the stale report
  is served instead with a contextual explanation note.
- Fresh reports clicked by either normal or elevated users are served from
  cache without error.
- Unauthenticated guests never trigger report generation when clicking a
  public stale report.
"""

from __future__ import annotations

import fnmatch
import re
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

import ph_stocks_advisor.infra.config as cfg
import ph_stocks_advisor.web.app as appmod
import ph_stocks_advisor.web.rate_limit as rlmod
import ph_stocks_advisor.web.tasks as tasksmod
from ph_stocks_advisor.infra.repository import ReportRecord, UserType
from ph_stocks_advisor.web.app import create_app


class FakeRedis:
    """In-memory Redis fake supporting atomic NX locks and rate-limit Lua reserve."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self._store.get(key)

    def set(self, key: str, value: str, ex: int | None = None, nx: bool = False) -> bool | None:  # noqa: A003
        if nx and key in self._store:
            return None
        self._store[key] = value
        return True

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
        key = args[0]
        limit = int(args[1])
        current = int(self._store.get(key, 0))
        if current >= limit:
            return [0, current]
        new = self.incr(key)
        return [1, new]


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def auth_env(monkeypatch, tmp_path, fake_redis):
    """Configured SQLite + Entra auth-enabled Flask test app with FakeRedis."""
    db_path = str(tmp_path / "stale_refresh.db")
    monkeypatch.setenv("DB_BACKEND", "sqlite")
    monkeypatch.setenv("SQLITE_PATH", db_path)
    monkeypatch.setenv("ENTRA_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("ENTRA_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("ENTRA_TENANT_ID", "test-tenant")
    monkeypatch.setenv("FLASK_SECRET_KEY", "stale-refresh-secret-key")
    monkeypatch.setenv("DAILY_ANALYSIS_LIMIT", "3")
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("WEBAUTHN_RP_ID", raising=False)

    cfg._reset_repository()
    cfg.get_settings.cache_clear()
    s = cfg.get_settings()
    s.db_backend = "sqlite"
    s.sqlite_path = db_path
    s.entra_client_id = "test-client-id"
    s.entra_client_secret = "test-secret"
    s.entra_tenant_id = "test-tenant"
    s.flask_secret_key = "stale-refresh-secret-key"
    s.google_client_id = ""
    s.webauthn_rp_id = ""
    s.daily_analysis_limit = 3

    with (
        patch.object(appmod, "get_redis", return_value=fake_redis),
        patch("ph_stocks_advisor.data.services.price.fetch_stock_price", return_value=None),
    ):
        app = create_app()
        app.config["TESTING"] = True
        yield app

    cfg._reset_repository()
    cfg.get_settings.cache_clear()


def _login(client, *, email: str = "investor@example.com", user_type: int = UserType.NORMAL) -> dict[str, str]:
    """Sign a user into the test client session and return CSRF headers."""
    csrf = "csrf-test-token"
    with client.session_transaction() as sess:
        sess["_csrf_token"] = csrf
        sess["user"] = {
            "name": "Test Investor",
            "email": email,
            "oid": f"oid-{email}",
            "provider": "microsoft",
            "user_type": int(user_type),
        }
    return {"X-CSRFToken": csrf}


def _seed_report(symbol: str, created_at: datetime, *, summary: str = "**Executive Summary:**\nOld analysis.") -> int:
    repo = cfg.get_repository()
    report_id = repo.save(
        ReportRecord(
            id=None,
            symbol=symbol,
            verdict="BUY",
            summary=summary,
            price_section="Price details",
            dividend_section="Dividend details",
            movement_section="Movement details",
            valuation_section="Valuation details",
            controversy_section="Controversy details",
            created_at=created_at,
            score=70,
        )
    )
    repo.add_user_symbol("investor@example.com", symbol)
    return report_id


def _patch_market(monkeypatch, *, is_open: bool, last_close: datetime) -> None:
    monkeypatch.setattr(appmod.trading_calendar, "is_market_open", lambda now=None: is_open)
    monkeypatch.setattr(appmod.trading_calendar, "last_trading_close", lambda now=None: last_close)
    monkeypatch.setattr(appmod.trading_calendar, "next_trading_close", lambda now=None: last_close + timedelta(days=1))
    monkeypatch.setattr(
        appmod.trading_calendar,
        "next_close_label",
        lambda now=None: "after Monday's 3:00 PM PHT close",
    )


class TestStaleReportClickBehaviour:
    """Clicking a stale report regenerates it when conditions permit, else shows the cached report."""

    def test_logged_user_clicking_stale_report_when_market_closed_dispatches_new_run(
        self, auth_env, fake_redis, monkeypatch
    ):
        close = datetime(2026, 11, 6, 7, 0, tzinfo=UTC)  # Fri 3:00 PM PHT
        _patch_market(monkeypatch, is_open=False, last_close=close)
        _seed_report("BDO", close - timedelta(days=3))

        client = auth_env.test_client()
        headers = _login(client)

        with patch.object(tasksmod.analyse_stock, "apply_async") as mock_dispatch:
            resp = client.post(
                "/analyse",
                data={"symbol": "BDO", "refresh_stale": "1"},
                headers=headers,
            )

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["status"] == "started"
        assert body["stale_refresh"] is True
        assert body["symbol"] == "BDO"
        assert body["task_id"]
        mock_dispatch.assert_called_once_with(
            args=["BDO"],
            kwargs={"user_id": "investor@example.com"},
            task_id=body["task_id"],
        )
        assert fake_redis.get("analysis:inflight:BDO") == body["task_id"]
        assert rlmod.get_remaining(fake_redis, "investor@example.com", 3) == 2

    def test_logged_user_clicking_stale_report_joins_existing_inflight_run(self, auth_env, fake_redis, monkeypatch):
        close = datetime(2026, 11, 6, 7, 0, tzinfo=UTC)
        _patch_market(monkeypatch, is_open=False, last_close=close)
        _seed_report("BDO", close - timedelta(days=2))
        fake_redis.set("analysis:inflight:BDO", "inflight-task-99", ex=600)

        client = auth_env.test_client()
        headers = _login(client)

        with patch.object(tasksmod.analyse_stock, "apply_async") as mock_dispatch:
            resp = client.post(
                "/analyse",
                data={"symbol": "BDO", "refresh_stale": "1"},
                headers=headers,
            )

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["status"] == "joined"
        assert body["stale_refresh"] is True
        assert body["task_id"] == "inflight-task-99"
        mock_dispatch.assert_not_called()
        # Joining an in-flight run does not consume a quota slot
        assert rlmod.get_remaining(fake_redis, "investor@example.com", 3) == 3

    def test_clicking_fresh_report_serves_cached_for_both_normal_and_elevated_users(self, auth_env, monkeypatch):
        close = datetime(2026, 11, 6, 7, 0, tzinfo=UTC)
        _patch_market(monkeypatch, is_open=False, last_close=close)
        report_id = _seed_report("TEL", close + timedelta(hours=2))

        # Normal user clicking fresh report -> cached
        normal_client = auth_env.test_client()
        normal_headers = _login(normal_client, email="normal@example.com", user_type=UserType.NORMAL)
        with patch.object(tasksmod.analyse_stock, "apply_async") as mock_dispatch:
            resp_normal = normal_client.post(
                "/analyse",
                data={"symbol": "TEL", "refresh_stale": "1"},
                headers=normal_headers,
            )
        assert resp_normal.status_code == 200
        assert resp_normal.get_json() == {"status": "cached", "symbol": "TEL", "report_id": report_id}
        mock_dispatch.assert_not_called()

        # Elevated user clicking fresh report -> cached (NOT 429 cooldown)
        elevated_client = auth_env.test_client()
        elevated_headers = _login(elevated_client, email="vip@example.com", user_type=UserType.ELEVATED)
        with patch.object(tasksmod.analyse_stock, "apply_async") as mock_dispatch:
            resp_click = elevated_client.post(
                "/analyse",
                data={"symbol": "TEL", "refresh_stale": "1"},
                headers=elevated_headers,
            )
            # Explicit re-analyse submission without refresh_stale still hits 429 cooldown
            resp_explicit = elevated_client.post(
                "/analyse",
                data={"symbol": "TEL"},
                headers=elevated_headers,
            )
        assert resp_click.status_code == 200
        assert resp_click.get_json() == {"status": "cached", "symbol": "TEL", "report_id": report_id}
        assert resp_explicit.status_code == 429
        mock_dispatch.assert_not_called()

    def test_elevated_user_clicking_stale_report_regenerates_without_quota_limit(
        self, auth_env, fake_redis, monkeypatch
    ):
        close = datetime(2026, 11, 6, 7, 0, tzinfo=UTC)
        _patch_market(monkeypatch, is_open=False, last_close=close)
        _seed_report("SM", close - timedelta(days=5))
        # Seed quota well past normal limit — elevated users bypass it
        fake_redis.set(rlmod._daily_key("vip@example.com"), "10")

        client = auth_env.test_client()
        headers = _login(client, email="vip@example.com", user_type=UserType.ELEVATED)

        with patch.object(tasksmod.analyse_stock, "apply_async") as mock_dispatch:
            resp = client.post(
                "/analyse",
                data={"symbol": "SM", "refresh_stale": "1"},
                headers=headers,
            )

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["status"] == "started"
        assert body["stale_refresh"] is True
        mock_dispatch.assert_called_once()

    def test_logged_user_clicking_stale_report_during_market_hours_serves_stale_report(self, auth_env, monkeypatch):
        close = datetime(2026, 11, 6, 7, 0, tzinfo=UTC)
        _patch_market(monkeypatch, is_open=True, last_close=close)
        report_id = _seed_report("ALI", close - timedelta(days=2))

        client = auth_env.test_client()
        headers = _login(client)

        with patch.object(tasksmod.analyse_stock, "apply_async") as mock_dispatch:
            resp = client.post(
                "/analyse",
                data={"symbol": "ALI", "refresh_stale": "1"},
                headers=headers,
            )

        assert resp.status_code == 200
        assert resp.get_json() == {"status": "cached", "symbol": "ALI", "report_id": report_id}
        mock_dispatch.assert_not_called()

        page = client.get("/report/ALI")
        assert page.status_code == 200
        html = page.get_data(as_text=True)
        assert "Market is open — showing the last report." in html

    def test_logged_user_clicking_stale_report_at_daily_limit_serves_stale_report_with_note(
        self, auth_env, fake_redis, monkeypatch
    ):
        close = datetime(2026, 11, 6, 7, 0, tzinfo=UTC)
        _patch_market(monkeypatch, is_open=False, last_close=close)
        report_id = _seed_report("JFC", close - timedelta(days=4), summary="**Executive Summary:**\nStale JFC.")
        # Exhaust normal user's daily quota (3/3)
        fake_redis.set(rlmod._daily_key("investor@example.com"), "3")

        client = auth_env.test_client()
        headers = _login(client)

        with patch.object(tasksmod.analyse_stock, "apply_async") as mock_dispatch:
            resp = client.post(
                "/analyse",
                data={"symbol": "JFC", "refresh_stale": "1"},
                headers=headers,
            )
            # Explicit new-stock analysis without refresh_stale still returns 429
            resp_new = client.post(
                "/analyse",
                data={"symbol": "NEWSTOCK"},
                headers=headers,
            )

        assert resp.status_code == 200
        assert resp.get_json() == {"status": "cached", "symbol": "JFC", "report_id": report_id}
        assert resp_new.status_code == 429
        mock_dispatch.assert_not_called()
        # Inflight lock must be released so another user with quota can still refresh JFC
        assert fake_redis.get("analysis:inflight:JFC") is None

        page = client.get("/report/JFC")
        assert page.status_code == 200
        html = page.get_data(as_text=True)
        assert "Stale JFC." in html
        assert "Daily analysis limit reached — showing the last report." in html

    def test_unauthenticated_guest_clicking_stale_public_report_never_triggers_generation(self, auth_env, monkeypatch):
        close = datetime(2026, 11, 6, 7, 0, tzinfo=UTC)
        _patch_market(monkeypatch, is_open=False, last_close=close)
        report_id = _seed_report("BDO", close - timedelta(days=3))

        guest_client = auth_env.test_client()
        landing_html = guest_client.get("/").get_data(as_text=True)
        assert 'data-authenticated="false"' in landing_html

        with guest_client.session_transaction() as sess:
            sess["_csrf_token"] = "guest-csrf"

        with patch.object(tasksmod.analyse_stock, "apply_async") as mock_dispatch:
            resp = guest_client.post(
                "/analyse",
                data={"symbol": "BDO", "refresh_stale": "1"},
                headers={"X-CSRFToken": "guest-csrf"},
            )

        assert resp.status_code == 200
        assert resp.get_json() == {"status": "cached", "symbol": "BDO", "report_id": report_id}
        mock_dispatch.assert_not_called()


class TestStaleRefreshFrontendWiring:
    """Verify the rendered HTML & JS assets wire notice banners and report-link interception."""

    def test_logged_in_pages_expose_auth_state_notice_banner_and_refresh_handlers(self, auth_env, monkeypatch):
        close = datetime(2026, 11, 6, 7, 0, tzinfo=UTC)
        _patch_market(monkeypatch, is_open=False, last_close=close)
        _seed_report("BDO", close - timedelta(days=2))

        client = auth_env.test_client()
        _login(client)

        landing_html = client.get("/").get_data(as_text=True)
        assert 'data-authenticated="true"' in landing_html
        assert 'id="notice-area"' in landing_html
        assert 'id="notice-text"' in landing_html
        assert 'class="stock-chip" data-symbol="BDO"' in landing_html
        assert 'class="sidebar-ticker" data-symbol="BDO"' in landing_html
        # Strictly preserve CSP (no inline <script> blocks on landing page)
        assert re.findall(r"<script(?![^>]*\bsrc=)[^>]*>", landing_html, flags=re.IGNORECASE) == []

        app_js = client.get("/static/app.js").get_data(as_text=True)
        assert 'closest(".stock-chip, .sidebar-ticker")' in app_js
        assert "refresh_stale=1" in app_js
        assert "flashNotice(staleRefreshMessage(" in app_js

        pwa_js = client.get("/static/pwa.js").get_data(as_text=True)
        assert 'closest(".sidebar-ticker")' in pwa_js
        assert "refresh_stale=1" in pwa_js
        assert "ph_advisor_stale_notice" in pwa_js
