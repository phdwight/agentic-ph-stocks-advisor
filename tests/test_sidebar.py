"""
Tests for the recent-history sidebar shown on every page.

Behaviour under test:
- The sidebar groups the user's recently analysed tickers by the date
  they were created.
- Each ticker entry shows its symbol and a verdict badge (BUY / NOT BUY).
- The sidebar is hidden when no user is logged in OR the history is
  empty.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from ph_stocks_advisor.infra.repository import ReportRecord
from ph_stocks_advisor.web.app import create_app


def _record(symbol: str, verdict: str, created_at: datetime) -> ReportRecord:
    return ReportRecord(
        id=hash((symbol, created_at)) & 0xFFFF,
        symbol=symbol,
        verdict=verdict,
        summary="",
        price_section="",
        dividend_section="",
        movement_section="",
        valuation_section="",
        controversy_section="",
        created_at=created_at,
    )


@pytest.fixture
def anon_app(monkeypatch):
    monkeypatch.delenv("ENTRA_CLIENT_ID", raising=False)
    monkeypatch.delenv("ENTRA_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("DB_BACKEND", "sqlite")
    monkeypatch.setenv("SQLITE_PATH", ":memory:")

    from ph_stocks_advisor.infra.config import get_settings

    get_settings.cache_clear()
    s = get_settings()
    s.entra_client_id = ""
    s.entra_client_secret = ""
    s.google_client_id = ""
    s.google_client_secret = ""

    app = create_app()
    app.config["TESTING"] = True
    yield app
    get_settings.cache_clear()


@pytest.fixture
def anon_client(anon_app):
    return anon_app.test_client()


class TestSidebarHistory:
    @patch("ph_stocks_advisor.web.app.get_repository")
    def test_sidebar_groups_tickers_by_date(self, mock_repo, anon_client):
        today = datetime(2026, 4, 26, 9, 0, tzinfo=UTC)
        yesterday = today - timedelta(days=1)
        repo = MagicMock()
        repo.list_user_symbols.return_value = [
            _record("TEL", "BUY", today),
            _record("SM", "NOT BUY", today),
            _record("BDO", "BUY", yesterday),
        ]
        repo.list_recent_symbols.return_value = []
        mock_repo.return_value = repo

        resp = anon_client.get("/")
        body = resp.get_data(as_text=True)

        assert resp.status_code == 200
        # Sidebar container present
        assert 'class="sidebar"' in body
        # Both date headers present, in order
        today_label = today.strftime("%b %d, %Y")
        yesterday_label = yesterday.strftime("%b %d, %Y")
        assert today_label in body
        assert yesterday_label in body
        assert body.index(today_label) < body.index(yesterday_label)
        # Symbols rendered under their dates
        assert "TEL" in body
        assert "SM" in body
        assert "BDO" in body
        # Verdict badges rendered
        assert "buy" in body.lower()

    @patch("ph_stocks_advisor.web.app.get_repository")
    def test_sidebar_hidden_when_history_empty(self, mock_repo, anon_client):
        repo = MagicMock()
        repo.list_user_symbols.return_value = []
        repo.list_recent_symbols.return_value = []
        mock_repo.return_value = repo

        resp = anon_client.get("/")
        body = resp.get_data(as_text=True)

        assert resp.status_code == 200
        assert 'class="sidebar"' not in body

    @patch("ph_stocks_advisor.web.app.get_repository")
    def test_sidebar_survives_repository_failure(self, mock_repo, anon_client):
        repo = MagicMock()
        repo.list_user_symbols.side_effect = RuntimeError("db down")
        repo.list_recent_symbols.side_effect = RuntimeError("db down")
        mock_repo.return_value = repo

        resp = anon_client.get("/")
        # Page still renders even if the sidebar query fails
        assert resp.status_code == 200
        assert 'class="sidebar"' not in resp.get_data(as_text=True)
