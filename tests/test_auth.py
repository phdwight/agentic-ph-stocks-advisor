"""
Tests for the Entra ID authentication module.

Verifies the OAuth2/OIDC flow (login → callback → session) and the
``login_required`` decorator behaviour.  MSAL and external HTTP calls
are mocked; logic under test stays real.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from ph_stocks_advisor.web.app import create_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def _entra_env(monkeypatch):
    """Set Entra ID environment variables for testing."""
    monkeypatch.setenv("ENTRA_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("ENTRA_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("ENTRA_TENANT_ID", "test-tenant")
    monkeypatch.setenv("FLASK_SECRET_KEY", "test-secret-key")
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    # Avoid real DB / Redis during auth tests
    monkeypatch.setenv("DB_BACKEND", "sqlite")
    monkeypatch.setenv("SQLITE_PATH", ":memory:")


@pytest.fixture
def _no_entra_env(monkeypatch):
    """Ensure no identity provider is configured (anonymous access)."""
    monkeypatch.delenv("ENTRA_CLIENT_ID", raising=False)
    monkeypatch.delenv("ENTRA_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("ENTRA_TENANT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)


@pytest.fixture
def _google_env(monkeypatch):
    """Set Google OAuth2 environment variables for testing."""
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-google-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-google-secret")
    monkeypatch.setenv("FLASK_SECRET_KEY", "test-secret-key")
    monkeypatch.delenv("ENTRA_CLIENT_ID", raising=False)
    monkeypatch.delenv("ENTRA_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("ENTRA_TENANT_ID", raising=False)
    monkeypatch.setenv("DB_BACKEND", "sqlite")
    monkeypatch.setenv("SQLITE_PATH", ":memory:")


@pytest.fixture
def app(_entra_env) -> Flask:
    """Create a Flask test app with Entra ID configured."""
    # Clear the lru_cache so env vars take effect.
    from ph_stocks_advisor.infra.config import get_settings

    get_settings.cache_clear()
    # Settings class attributes are evaluated at import time, so we must
    # set instance attributes on the cached Settings object to override.
    s = get_settings()
    s.entra_client_id = "test-client-id"
    s.entra_client_secret = "test-secret"
    s.entra_tenant_id = "test-tenant"
    s.flask_secret_key = "test-secret-key"

    application = create_app()
    application.config["TESTING"] = True
    yield application
    get_settings.cache_clear()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def anon_app(_no_entra_env) -> Flask:
    """Create a Flask test app *without* any auth provider (anonymous mode)."""
    from ph_stocks_advisor.infra.config import get_settings

    get_settings.cache_clear()
    s = get_settings()
    s.entra_client_id = ""
    s.entra_client_secret = ""
    s.entra_tenant_id = "common"
    s.google_client_id = ""
    s.google_client_secret = ""

    application = create_app()
    application.config["TESTING"] = True
    yield application
    get_settings.cache_clear()


@pytest.fixture
def anon_client(anon_app):
    return anon_app.test_client()


@pytest.fixture
def google_app(_google_env) -> Flask:
    """Create a Flask test app with Google OAuth2 configured."""
    from ph_stocks_advisor.infra.config import get_settings

    get_settings.cache_clear()
    s = get_settings()
    s.entra_client_id = ""
    s.entra_client_secret = ""
    s.entra_tenant_id = "common"
    s.google_client_id = "test-google-client-id"
    s.google_client_secret = "test-google-secret"
    s.flask_secret_key = "test-secret-key"

    application = create_app()
    application.config["TESTING"] = True
    yield application
    get_settings.cache_clear()


@pytest.fixture
def google_client(google_app):
    return google_app.test_client()


# ---------------------------------------------------------------------------
# Tests — unauthenticated access redirects to login
# ---------------------------------------------------------------------------


class TestLoginRequired:
    """Protected routes redirect unauthenticated users to /auth/login."""

    def test_index_redirects_when_not_logged_in(self, client):
        resp = client.get("/")
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["Location"]

    def test_report_redirects_when_not_logged_in(self, client):
        resp = client.get("/report/TEL")
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["Location"]

    def test_history_redirects_when_not_logged_in(self, client):
        resp = client.get("/history/TEL")
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["Location"]


# ---------------------------------------------------------------------------
# Tests — anonymous access when Entra ID is not configured
# ---------------------------------------------------------------------------


class TestAnonymousAccess:
    """When ENTRA_CLIENT_ID is empty, routes are accessible without login."""

    @patch("ph_stocks_advisor.web.app.get_repository")
    def test_index_accessible_without_login(self, mock_repo, anon_client):
        repo_instance = MagicMock()
        repo_instance.list_recent_symbols.return_value = []
        mock_repo.return_value = repo_instance
        resp = anon_client.get("/")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Tests — login page
# ---------------------------------------------------------------------------


class TestLoginPage:
    """The /auth/login route renders the login template."""

    def test_login_page_renders(self, client):
        resp = client.get("/auth/login")
        assert resp.status_code == 200
        assert b"Sign in with Microsoft" in resp.data

    def test_login_redirects_when_entra_not_configured(self, anon_client):
        resp = anon_client.get("/auth/login")
        assert resp.status_code == 302  # redirect to index


# ---------------------------------------------------------------------------
# Tests — sign-in redirect
# ---------------------------------------------------------------------------


class TestSignin:
    """The /auth/signin route redirects to Microsoft's auth endpoint."""

    @patch("ph_stocks_advisor.web.auth._build_msal_app")
    def test_signin_redirects_to_microsoft(self, mock_msal, client):
        mock_app = MagicMock()
        mock_app.get_authorization_request_url.return_value = (
            "https://login.microsoftonline.com/test-tenant/oauth2/v2.0/authorize?..."
        )
        mock_msal.return_value = mock_app

        resp = client.get("/auth/signin")
        assert resp.status_code == 302
        assert "login.microsoftonline.com" in resp.headers["Location"]


# ---------------------------------------------------------------------------
# Tests — callback
# ---------------------------------------------------------------------------


class TestCallback:
    """The /auth/callback route exchanges the code for tokens."""

    @patch("ph_stocks_advisor.web.auth._build_msal_app")
    def test_callback_sets_session_user(self, mock_msal, client):
        mock_app = MagicMock()
        mock_app.acquire_token_by_authorization_code.return_value = {
            "id_token_claims": {
                "name": "Juan Dela Cruz",
                "preferred_username": "juan@example.com",
                "oid": "user-oid-123",
            }
        }
        mock_msal.return_value = mock_app

        with client.session_transaction() as sess:
            sess["auth_state"] = "test-state"

        resp = client.get("/auth/callback?code=test-code&state=test-state")
        assert resp.status_code == 302  # redirect to index

        with client.session_transaction() as sess:
            assert sess["user"]["name"] == "Juan Dela Cruz"
            assert sess["user"]["email"] == "juan@example.com"
            assert sess["user"]["provider"] == "microsoft"

    def test_callback_state_mismatch_redirects_to_login(self, client):
        with client.session_transaction() as sess:
            sess["auth_state"] = "good-state"

        resp = client.get("/auth/callback?code=test-code&state=bad-state")
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["Location"]

    @patch("ph_stocks_advisor.web.auth._build_msal_app")
    def test_callback_token_error_shows_login(self, mock_msal, client):
        mock_app = MagicMock()
        mock_app.acquire_token_by_authorization_code.return_value = {
            "error": "invalid_grant",
            "error_description": "Code expired.",
        }
        mock_msal.return_value = mock_app

        with client.session_transaction() as sess:
            sess["auth_state"] = "test-state"

        resp = client.get("/auth/callback?code=bad-code&state=test-state")
        assert resp.status_code == 200
        assert b"Code expired" in resp.data


# ---------------------------------------------------------------------------
# Tests — logout
# ---------------------------------------------------------------------------


class TestLogout:
    """The /auth/logout route clears the session and redirects."""

    def test_logout_clears_session(self, client):
        # Simulate a logged-in Microsoft user.
        with client.session_transaction() as sess:
            sess["user"] = {
                "name": "Test",
                "email": "t@e.com",
                "oid": "123",
                "provider": "microsoft",
            }

        resp = client.get("/auth/logout")
        assert resp.status_code == 302
        assert "login.microsoftonline.com" in resp.headers["Location"]

        with client.session_transaction() as sess:
            assert "user" not in sess


# ---------------------------------------------------------------------------
# Tests — authenticated access
# ---------------------------------------------------------------------------


class TestAuthenticatedAccess:
    """Logged-in users can access protected routes."""

    @patch("ph_stocks_advisor.web.app.get_repository")
    def test_index_accessible_when_logged_in(self, mock_repo, client):
        repo_instance = MagicMock()
        repo_instance.list_recent_symbols.return_value = []
        mock_repo.return_value = repo_instance

        with client.session_transaction() as sess:
            sess["user"] = {"name": "Test", "email": "t@e.com", "oid": "123"}

        resp = client.get("/")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Tests — Google OAuth2 sign-in
# ---------------------------------------------------------------------------


class TestGoogleSignin:
    """The /auth/google/signin route redirects to Google's auth endpoint."""

    def test_google_signin_redirects_to_google(self, google_client):
        resp = google_client.get("/auth/google/signin")
        assert resp.status_code == 302
        assert "accounts.google.com" in resp.headers["Location"]
        assert "test-google-client-id" in resp.headers["Location"]

    def test_login_page_shows_google_button(self, google_client):
        resp = google_client.get("/auth/login")
        assert resp.status_code == 200
        assert b"Sign in with Google" in resp.data


class TestGoogleCallback:
    """The /auth/google/callback route exchanges the code for user info."""

    @patch("ph_stocks_advisor.web.auth.http_requests.get")
    @patch("ph_stocks_advisor.web.auth.http_requests.post")
    def test_google_callback_sets_session_user(
        self, mock_post, mock_get, google_client
    ):
        # Mock token exchange.
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"access_token": "fake-access-token"},
        )
        # Mock userinfo.
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "name": "Maria Santos",
                "email": "maria@gmail.com",
                "sub": "google-sub-456",
            },
        )

        with google_client.session_transaction() as sess:
            sess["google_state"] = "test-google-state"

        resp = google_client.get(
            "/auth/google/callback?code=test-code&state=test-google-state"
        )
        assert resp.status_code == 302

        with google_client.session_transaction() as sess:
            assert sess["user"]["name"] == "Maria Santos"
            assert sess["user"]["email"] == "maria@gmail.com"
            assert sess["user"]["provider"] == "google"

    def test_google_callback_state_mismatch(self, google_client):
        with google_client.session_transaction() as sess:
            sess["google_state"] = "good-state"

        resp = google_client.get(
            "/auth/google/callback?code=test-code&state=bad-state"
        )
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["Location"]

    @patch("ph_stocks_advisor.web.auth.http_requests.post")
    def test_google_callback_token_error(self, mock_post, google_client):
        mock_post.return_value = MagicMock(
            status_code=400, text="invalid_grant"
        )

        with google_client.session_transaction() as sess:
            sess["google_state"] = "test-state"

        resp = google_client.get(
            "/auth/google/callback?code=bad-code&state=test-state"
        )
        assert resp.status_code == 200
        assert b"Could not exchange" in resp.data


class TestGoogleLogout:
    """Google users get redirected to the login page on logout."""

    def test_google_user_logout_redirects_to_login(self, google_client):
        with google_client.session_transaction() as sess:
            sess["user"] = {
                "name": "Maria",
                "email": "m@gmail.com",
                "oid": "456",
                "provider": "google",
            }

        resp = google_client.get("/auth/logout")
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["Location"]

        with google_client.session_transaction() as sess:
            assert "user" not in sess
