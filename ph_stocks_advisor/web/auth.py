"""
Authentication blueprint -- Microsoft Entra ID + Google OAuth2.

Single Responsibility: handles all OAuth2 / OpenID Connect interactions
for sign-in.  Supports two identity providers:

* **Microsoft Entra ID** -- via MSAL (authorization-code flow), with
  optional FIDO2 passkey support configured on the tenant side.
* **Google** -- standard OAuth2 authorization-code flow using plain HTTP
  requests (no extra library).

Dependency Inversion: depends on the Settings abstraction from
``infra.config`` rather than reading environment variables directly.
"""

from __future__ import annotations

import logging
import uuid
from functools import wraps
from typing import Any, Callable
from urllib.parse import urlencode

import msal
import requests as http_requests
from flask import (
    Blueprint,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from ph_stocks_advisor.infra.config import get_repository, get_settings
from ph_stocks_advisor.infra.repository import UserRecord

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

# Microsoft OIDC scopes — User.Read gives us the signed-in user's profile.
_SCOPES = ["User.Read"]

# Google OAuth2 endpoints.
_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
_GOOGLE_SCOPES = "openid email profile"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_msal_app(
    authority: str | None = None,
    cache: msal.SerializableTokenCache | None = None,
) -> msal.ConfidentialClientApplication:
    """Build a confidential MSAL application instance."""
    settings = get_settings()
    return msal.ConfidentialClientApplication(
        client_id=settings.entra_client_id,
        client_credential=settings.entra_client_secret,
        authority=authority or settings.entra_authority,
        token_cache=cache,
    )


def _get_token_cache() -> msal.SerializableTokenCache:
    """Return a throwaway MSAL token cache for the authorization-code exchange.

    We intentionally do NOT persist the cache in the session because it
    can easily exceed the 4 KB browser cookie limit and browsers silently
    drop oversized cookies — causing an infinite login loop.  We only
    need the ID-token claims (name / email) which are extracted during
    the callback; we never call the Graph API later so there is no need
    to keep access/refresh tokens around.
    """
    return msal.SerializableTokenCache()


# Default identity used when authentication is disabled (local dev).
_DEV_USER: dict[str, str] = {
    "name": "Local Developer",
    "email": "dev@localhost",
    "oid": "local-dev",
    "provider": "local",
}


def get_current_user() -> dict[str, Any] | None:
    """Return the currently signed-in user dict, or ``None``.

    When authentication is disabled (no identity provider configured),
    a deterministic dev user is returned so that per-user symbol
    tracking still works during local development.
    """
    user = session.get("user")
    if user:
        return user
    settings = get_settings()
    if not settings.auth_enabled:
        return _DEV_USER
    return None


def login_required(f: Callable) -> Callable:
    """Decorator that redirects unauthenticated users to the login page."""

    @wraps(f)
    def decorated(*args: Any, **kwargs: Any) -> Any:
        settings = get_settings()
        # If no identity provider is configured, allow anonymous access.
        if not settings.auth_enabled:
            return f(*args, **kwargs)
        if get_current_user() is None:
            session["next_url"] = request.url
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)

    return decorated


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@auth_bp.route("/login")
def login():
    """Render the login page with available sign-in buttons."""
    settings = get_settings()
    if not settings.auth_enabled:
        return redirect(url_for("index"))
    return render_template(
        "login.html",
        entra_enabled=settings.entra_enabled,
        google_enabled=settings.google_enabled,
    )


@auth_bp.route("/signin")
def signin():
    """Initiate the Entra ID authorization-code flow."""
    settings = get_settings()
    session["auth_state"] = str(uuid.uuid4())

    app = _build_msal_app()
    auth_url = app.get_authorization_request_url(
        scopes=_SCOPES,
        state=session["auth_state"],
        redirect_uri=request.url_root.rstrip("/") + settings.entra_redirect_path,
        # Prompt the user to select an account — allows passkey selection.
        prompt="select_account",
    )
    return redirect(auth_url)


@auth_bp.route("/callback")
def callback():
    """Handle the redirect from Entra ID after authentication."""
    settings = get_settings()

    # Validate the state parameter to prevent CSRF.
    if request.args.get("state") != session.get("auth_state"):
        logger.warning("State mismatch in auth callback.")
        return redirect(url_for("auth.login"))

    if "error" in request.args:
        logger.error(
            "Auth error: %s — %s",
            request.args.get("error"),
            request.args.get("error_description"),
        )
        return render_template(
            "login.html",
            error=request.args.get("error_description", "Authentication failed."),
        )

    if "code" not in request.args:
        return redirect(url_for("auth.login"))

    cache = _get_token_cache()
    msal_app = _build_msal_app(cache=cache)
    result = msal_app.acquire_token_by_authorization_code(
        code=request.args["code"],
        scopes=_SCOPES,
        redirect_uri=request.url_root.rstrip("/") + settings.entra_redirect_path,
    )

    if "error" in result:
        logger.error(
            "Token acquisition failed: %s — %s",
            result.get("error"),
            result.get("error_description"),
        )
        return render_template(
            "login.html",
            error=result.get("error_description", "Could not acquire token."),
        )

    # Store only the minimal user claims from the ID token.
    # We do NOT persist the MSAL token cache — it is large and would
    # blow past the 4 KB cookie limit.
    id_claims = result.get("id_token_claims", {})
    session["user"] = {
        "name": id_claims.get("name", ""),
        "email": id_claims.get("preferred_username", ""),
        "oid": id_claims.get("oid", ""),
        "provider": "microsoft",
    }

    # Persist user in the database (upsert).
    try:
        repo = get_repository()
        repo.save_user(UserRecord(
            oid=session["user"]["oid"],
            name=session["user"]["name"],
            email=session["user"]["email"],
            provider="microsoft",
        ))
    except Exception:
        logger.exception("Failed to persist user record")

    logger.info("User signed in: %s", session["user"].get("email"))

    # Redirect to where the user originally wanted to go.
    next_url = session.pop("next_url", None) or url_for("index")
    return redirect(next_url)


# ---------------------------------------------------------------------------
# Google OAuth2 routes
# ---------------------------------------------------------------------------


@auth_bp.route("/google/signin")
def google_signin():
    """Initiate Google OAuth2 authorization-code flow."""
    settings = get_settings()
    session["google_state"] = str(uuid.uuid4())

    redirect_uri = (
        request.url_root.rstrip("/") + settings.google_redirect_path
    )
    params = urlencode(
        {
            "client_id": settings.google_client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": _GOOGLE_SCOPES,
            "state": session["google_state"],
            "access_type": "online",
            "prompt": "select_account",
        }
    )
    return redirect(f"{_GOOGLE_AUTH_URL}?{params}")


@auth_bp.route("/google/callback")
def google_callback():
    """Handle the redirect from Google after authentication."""
    settings = get_settings()

    # CSRF check.
    if request.args.get("state") != session.get("google_state"):
        logger.warning("State mismatch in Google auth callback.")
        return redirect(url_for("auth.login"))

    if "error" in request.args:
        logger.error("Google auth error: %s", request.args.get("error"))
        return render_template(
            "login.html",
            error=request.args.get("error_description", "Google authentication failed."),
            entra_enabled=settings.entra_enabled,
            google_enabled=settings.google_enabled,
        )

    code = request.args.get("code")
    if not code:
        return redirect(url_for("auth.login"))

    redirect_uri = (
        request.url_root.rstrip("/") + settings.google_redirect_path
    )

    # Exchange authorization code for tokens.
    token_resp = http_requests.post(
        _GOOGLE_TOKEN_URL,
        data={
            "code": code,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=10,
    )

    if token_resp.status_code != 200:
        logger.error("Google token exchange failed: %s", token_resp.text)
        return render_template(
            "login.html",
            error="Could not exchange Google authorization code.",
            entra_enabled=settings.entra_enabled,
            google_enabled=settings.google_enabled,
        )

    tokens = token_resp.json()
    access_token = tokens.get("access_token")

    # Fetch user profile from Google.
    userinfo_resp = http_requests.get(
        _GOOGLE_USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )

    if userinfo_resp.status_code != 200:
        logger.error("Google userinfo request failed: %s", userinfo_resp.text)
        return render_template(
            "login.html",
            error="Could not retrieve Google user profile.",
            entra_enabled=settings.entra_enabled,
            google_enabled=settings.google_enabled,
        )

    userinfo = userinfo_resp.json()
    session["user"] = {
        "name": userinfo.get("name", ""),
        "email": userinfo.get("email", ""),
        "oid": userinfo.get("sub", ""),
        "provider": "google",
    }

    # Persist user in the database (upsert).
    try:
        repo = get_repository()
        repo.save_user(UserRecord(
            oid=session["user"]["oid"],
            name=session["user"]["name"],
            email=session["user"]["email"],
            provider="google",
        ))
    except Exception:
        logger.exception("Failed to persist user record")

    logger.info("Google user signed in: %s", session["user"].get("email"))

    next_url = session.pop("next_url", None) or url_for("index")
    return redirect(next_url)


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------


@auth_bp.route("/logout")
def logout():
    """Sign the user out locally and redirect to the appropriate logout endpoint."""
    settings = get_settings()
    provider = session.get("user", {}).get("provider")
    session.clear()

    if not settings.auth_enabled:
        return redirect(url_for("index"))

    # If the user signed in via Microsoft, redirect to Entra's logout.
    if provider != "google" and settings.entra_enabled:
        logout_url = (
            f"{settings.entra_authority}/oauth2/v2.0/logout"
            f"?post_logout_redirect_uri={request.url_root.rstrip('/')}"
        )
        return redirect(logout_url)

    # Otherwise (Google or unknown), just redirect to the login page.
    return redirect(url_for("auth.login"))
