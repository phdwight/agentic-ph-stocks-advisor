"""
Microsoft Entra ID authentication blueprint.

Single Responsibility: handles all OAuth2 / OpenID Connect interactions
with Microsoft Entra ID (formerly Azure AD).  Passkey (FIDO2) support
is configured on the Entra ID tenant side; this module drives the
standard authorization-code flow via MSAL.

Dependency Inversion: depends on the Settings abstraction from
``infra.config`` rather than reading environment variables directly.
"""

from __future__ import annotations

import logging
import uuid
from functools import wraps
from typing import Any, Callable

import msal
from flask import (
    Blueprint,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from ph_stocks_advisor.infra.config import get_settings

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

# OIDC scopes — User.Read gives us the signed-in user's profile.
_SCOPES = ["User.Read"]


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


def get_current_user() -> dict[str, Any] | None:
    """Return the currently signed-in user dict, or ``None``."""
    return session.get("user")


def login_required(f: Callable) -> Callable:
    """Decorator that redirects unauthenticated users to the login page."""

    @wraps(f)
    def decorated(*args: Any, **kwargs: Any) -> Any:
        settings = get_settings()
        # If Entra ID is not configured, allow anonymous access.
        if not settings.entra_client_id or settings.entra_client_id == "NOTSET":
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
    """Render the login page with a 'Sign in with Microsoft' button."""
    settings = get_settings()
    if not settings.entra_client_id or settings.entra_client_id == "NOTSET":
        return redirect(url_for("index"))
    return render_template("login.html")


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
    }

    logger.info("User signed in: %s", session["user"].get("email"))

    # Redirect to where the user originally wanted to go.
    next_url = session.pop("next_url", None) or url_for("index")
    return redirect(next_url)


@auth_bp.route("/logout")
def logout():
    """Sign the user out locally and redirect to Entra ID logout."""
    settings = get_settings()
    session.clear()

    if not settings.entra_client_id:
        return redirect(url_for("index"))

    # Redirect to Microsoft's logout endpoint.
    logout_url = (
        f"{settings.entra_authority}/oauth2/v2.0/logout"
        f"?post_logout_redirect_uri={request.url_root.rstrip('/')}"
    )
    return redirect(logout_url)
