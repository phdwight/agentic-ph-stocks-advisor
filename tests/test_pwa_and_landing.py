"""Tests for PWA endpoints, manifest/icons, service worker, and modernized landing page."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from ph_stocks_advisor.infra.repository import ReportRecord
from ph_stocks_advisor.web.app import _asset_rev, create_app


@pytest.fixture
def anon_client(monkeypatch):
    monkeypatch.delenv("ENTRA_CLIENT_ID", raising=False)
    monkeypatch.delenv("ENTRA_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("WEBAUTHN_RP_ID", raising=False)
    monkeypatch.setenv("DB_BACKEND", "sqlite")
    monkeypatch.setenv("SQLITE_PATH", ":memory:")

    from ph_stocks_advisor.infra.config import get_settings

    get_settings.cache_clear()
    s = get_settings()
    s.entra_client_id = ""
    s.entra_client_secret = ""
    s.google_client_id = ""
    s.google_client_secret = ""
    s.webauthn_rp_id = ""

    app = create_app()
    app.config["TESTING"] = True
    yield app.test_client()
    get_settings.cache_clear()


class TestPwaManifestAndServiceWorker:
    def test_manifest_endpoint_is_valid_and_installable(self, anon_client):
        resp = anon_client.get("/manifest.webmanifest")
        assert resp.status_code == 200
        assert resp.mimetype == "application/manifest+json"

        data = resp.get_json()
        assert data["name"] == "PH Stocks Advisor"
        assert data["short_name"] == "PH Stocks"
        assert data["display"] == "standalone"
        assert data["start_url"] == "/?source=pwa"
        assert data["scope"] == "/"

        purposes = {(icon["sizes"], icon["purpose"]) for icon in data["icons"]}
        assert ("192x192", "any") in purposes
        assert ("512x512", "any") in purposes
        assert ("512x512", "maskable") in purposes

        # Every icon declared in the manifest must actually be served
        for icon in data["icons"]:
            path = icon["src"].split("?")[0]
            icon_resp = anon_client.get(path)
            assert icon_resp.status_code == 200, f"Missing icon at {path}"
            assert len(icon_resp.data) > 100

    def test_apple_touch_icon_is_served(self, anon_client):
        resp = anon_client.get("/static/icons/apple-touch-icon.png")
        assert resp.status_code == 200
        assert resp.data.startswith(b"\x89PNG\r\n\x1a\n")

    def test_service_worker_served_at_root_scope_with_asset_rev(self, anon_client):
        resp = anon_client.get("/sw.js")
        assert resp.status_code == 200
        assert resp.mimetype == "application/javascript"
        assert resp.headers.get("Service-Worker-Allowed") == "/"
        assert "no-store" in (resp.headers.get("Cache-Control") or "")

        body = resp.get_data(as_text=True)
        assert f'const ASSET_REV = "{_asset_rev()}";' in body
        assert 'caches.match("/offline")' in body

    def test_offline_page_is_public(self, anon_client):
        resp = anon_client.get("/offline")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "offline" in html.lower()
        assert 'id="offline-retry-btn"' in html


class TestModernLandingPage:
    @patch("ph_stocks_advisor.web.app.trading_calendar.is_market_open", return_value=False)
    @patch("ph_stocks_advisor.web.app.get_repository")
    def test_landing_page_pwa_shell_and_csp_cleanliness(self, mock_repo, _mock_open, anon_client):
        repo = MagicMock()
        repo.list_recent_symbols.return_value = []
        repo.list_user_symbols.return_value = []
        mock_repo.return_value = repo

        resp = anon_client.get("/")
        assert resp.status_code == 200

        csp = resp.headers.get("Content-Security-Policy") or ""
        assert "worker-src 'self'" in csp
        assert "manifest-src 'self'" in csp

        html = resp.get_data(as_text=True)
        # Required PWA head metadata
        assert 'rel="manifest" href="/manifest.webmanifest"' in html
        assert "viewport-fit=cover" in html
        assert 'name="apple-mobile-web-app-capable" content="yes"' in html
        assert "apple-touch-icon.png" in html
        assert "pwa.js" in html

        # Strictly no inline <script> blocks (enforced by script-src 'self')
        inline_scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>", html, flags=re.IGNORECASE)
        assert inline_scripts == []

        # Market status pill, mobile tabbar, and all 6 specialists rendered
        assert "PSE Closed" in html
        assert 'class="mobile-tabbar"' in html
        for specialist in (
            "Price Analysis",
            "Valuation &amp; Fair Value",
            "Dividend Sustainability",
            "Momentum &amp; Trend",
            "Market Sentiment",
            "Controversy &amp; Risk",
        ):
            assert specialist in html

        # Recent section hidden when there are no stocks yet
        assert 'id="recent-stocks" style="display:none;"' in html

    @patch("ph_stocks_advisor.web.app.trading_calendar.is_market_open", return_value=True)
    @patch("ph_stocks_advisor.web.app.get_repository")
    def test_landing_page_shows_live_market_pill_and_recent_stocks(self, mock_repo, _mock_open, anon_client):
        record = ReportRecord(
            id=1,
            symbol="BDO",
            verdict="BUY",
            summary="ok",
            price_section="",
            dividend_section="",
            movement_section="",
            valuation_section="",
            controversy_section="",
            created_at=datetime.now(tz=UTC),
            score=78,
        )
        repo = MagicMock()
        repo.list_recent_symbols.return_value = [record]
        repo.list_user_symbols.return_value = [record]
        mock_repo.return_value = repo

        resp = anon_client.get("/")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "PSE Open" in html
        assert 'id="recent-stocks" style="display:none;"' not in html
        assert 'href="/report/BDO"' in html
