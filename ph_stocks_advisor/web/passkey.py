"""
Passkey (WebAuthn / FIDO2) sign-in blueprint.

Model: **passkey-only, open self-signup, email-first login.** Google/MS
OAuth remains available (via ``auth.py``) as an account-recovery fallback.

Ceremonies use py_webauthn. Registration/authentication challenges are held
in the Flask session between the two round-trips. Verification is always done
against the *configured* origin/RP ID (``settings.webauthn_*``), never values
inferred from the request — safe behind the cloudflared tunnel.

Anti-enumeration: ``login/begin`` returns a decoy ``allowCredentials`` for
unknown emails (deterministic from HMAC(secret, email)) so the response looks
identical whether or not the email is registered, and all auth failures return
the same generic message.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import uuid
from datetime import UTC, datetime

from flask import Blueprint, Response, jsonify, request, session
from flask.typing import ResponseReturnValue
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    AuthenticatorTransport,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from ph_stocks_advisor.infra.config import get_repository, get_settings
from ph_stocks_advisor.infra.repository import UserRecord, WebAuthnCredentialRecord
from ph_stocks_advisor.web.auth import _safe_redirect_url

logger = logging.getLogger(__name__)

passkey_bp = Blueprint("passkey", __name__, url_prefix="/auth/passkey")

# One generic message for every failure — never reveal *why* (anti-enumeration).
_GENERIC_LOGIN_ERR = "Couldn't sign you in with a passkey. Check the email and try again."
_GENERIC_REG_ERR = "Couldn't set up a passkey for that email. If you already have an account, sign in instead."
# Version of the terms a user accepts at sign-up — keep in step with
# ``web.app._DISCLAIMER_UPDATED`` whenever the disclaimer text changes.
_DISCLAIMER_VERSION = "2026-07-26"

_INVALID_EMAIL_ERR = "Enter a valid email address."
_NO_CONSENT_ERR = "You must read and accept the Disclaimer & Terms of Use to create an account."

# We don't verify that the address is deliverable (no email is sent) — this
# just rejects malformed input: local@domain.tld, no spaces, a dot in the domain.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _valid_email(email: str) -> bool:
    return bool(email) and len(email) <= 254 and _EMAIL_RE.match(email) is not None


def _user_handle(oid: str) -> bytes:
    """Stable, opaque 16-byte WebAuthn user handle derived from the account oid."""
    return hashlib.sha256(f"wauh:{oid}".encode()).digest()[:16]


def _decoy_credential_id(email: str) -> bytes:
    """Deterministic fake credential id for an unregistered email (anti-enumeration)."""
    secret = get_settings().flask_secret_key.encode()
    return hmac.new(secret, f"decoy:{email}".encode(), hashlib.sha256).digest()


def _session_user() -> dict | None:
    """The genuinely authenticated user (ignores the auth-disabled dev user)."""
    return session.get("user")


def _complete_login(oid: str, name: str, email: str) -> None:
    """Set the session user exactly like the OAuth flow, incl. DB user_type."""
    repo = get_repository()
    session["user"] = {
        "name": name,
        "email": email,
        "oid": oid,
        "provider": "passkey",
        "user_type": 0,
    }
    db_user = repo.get_user(oid)
    if db_user:
        session["user"]["user_type"] = db_user.user_type
    logger.info("Passkey sign-in: %s", email)


def _transports_from(raw_json: str) -> list[str]:
    try:
        return list(json.loads(raw_json).get("response", {}).get("transports", []) or [])
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Registration (self-signup, or add-a-device when already signed in)
# ---------------------------------------------------------------------------


@passkey_bp.route("/register/begin", methods=["POST"])
def register_begin() -> ResponseReturnValue:
    settings = get_settings()
    if not settings.passkey_enabled:
        return jsonify({"error": "Passkeys are not enabled."}), 404
    repo = get_repository()
    current = _session_user()
    data = request.get_json(silent=True) or {}

    if current:
        # Authenticated → adding another passkey to the existing account.
        oid, name, email, is_new = current["oid"], current["name"], current["email"], False
        existing = repo.list_webauthn_credentials(oid)
    else:
        email = (data.get("email") or "").strip().lower()
        name = (data.get("name") or "").strip() or email
        if not _valid_email(email):
            return jsonify({"error": _INVALID_EMAIL_ERR}), 400
        # Creating a NEW account requires accepting the disclaimer. Enforced
        # here, server-side: the checkbox in the UI is convenience only and
        # must never be the sole gate.
        if data.get("accept_disclaimer") is not True:
            return jsonify({"error": _NO_CONSENT_ERR}), 400
        # Existing account: don't let an anonymous caller attach a passkey
        # (account-takeover guard). Generic message — no "email taken" leak.
        if repo.get_user_by_email(email) is not None:
            return jsonify({"error": _GENERIC_REG_ERR}), 400
        oid, is_new, existing = f"passkey:{uuid.uuid4().hex}", True, []

    options = generate_registration_options(
        rp_id=settings.webauthn_rp_id,
        rp_name=settings.webauthn_rp_name,
        user_id=_user_handle(oid),
        user_name=email,
        user_display_name=name,
        exclude_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(c.credential_id)) for c in existing],
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.DISCOURAGED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
    )
    session["pk_reg_chal"] = bytes_to_base64url(options.challenge)
    session["pk_reg_oid"] = oid
    session["pk_reg_new"] = is_new
    session["pk_reg_name"] = name
    session["pk_reg_email"] = email
    session["pk_reg_consent"] = is_new  # only new accounts gave consent just now
    return Response(options_to_json(options), mimetype="application/json")


@passkey_bp.route("/register/complete", methods=["POST"])
def register_complete() -> ResponseReturnValue:
    settings = get_settings()
    if not settings.passkey_enabled:
        return jsonify({"error": "Passkeys are not enabled."}), 404
    repo = get_repository()

    chal = session.pop("pk_reg_chal", None)
    oid = session.pop("pk_reg_oid", None)
    is_new = session.pop("pk_reg_new", False)
    name = session.pop("pk_reg_name", "")
    email = session.pop("pk_reg_email", "")
    consented = session.pop("pk_reg_consent", False)
    if not chal or not oid:
        return jsonify({"error": _GENERIC_REG_ERR}), 400

    raw = request.get_data(as_text=True)
    try:
        verification = verify_registration_response(
            credential=raw,
            expected_challenge=base64url_to_bytes(chal),
            expected_rp_id=settings.webauthn_rp_id,
            expected_origin=settings.webauthn_origin,
            require_user_verification=False,
        )
    except Exception:
        logger.warning("Passkey registration verification failed", exc_info=True)
        return jsonify({"error": _GENERIC_REG_ERR}), 400

    if is_new:
        # Persist proof of consent: which version of the terms, and when.
        repo.save_user(
            UserRecord(
                oid=oid,
                name=name,
                email=email,
                provider="passkey",
                disclaimer_version=_DISCLAIMER_VERSION if consented else None,
                disclaimer_accepted_at=datetime.now(tz=UTC) if consented else None,
            )
        )

    repo.add_webauthn_credential(
        WebAuthnCredentialRecord(
            credential_id=bytes_to_base64url(verification.credential_id),
            user_oid=oid,
            public_key=bytes_to_base64url(verification.credential_public_key),
            sign_count=verification.sign_count,
            transports=_transports_from(raw),
            aaguid=str(verification.aaguid) if verification.aaguid else None,
            nickname=(request.args.get("nickname") or "Passkey"),
        )
    )

    # New signup logs the user in; add-a-device keeps the existing session.
    if not _session_user():
        _complete_login(oid, name, email)
    return jsonify({"ok": True, "redirect": _safe_redirect_url(session.pop("next_url", None))})


# ---------------------------------------------------------------------------
# Authentication (email-first)
# ---------------------------------------------------------------------------


@passkey_bp.route("/login/begin", methods=["POST"])
def login_begin() -> ResponseReturnValue:
    settings = get_settings()
    if not settings.passkey_enabled:
        return jsonify({"error": "Passkeys are not enabled."}), 404
    repo = get_repository()
    email = ((request.get_json(silent=True) or {}).get("email") or "").strip().lower()
    if not _valid_email(email):
        return jsonify({"error": _INVALID_EMAIL_ERR}), 400

    creds: list[WebAuthnCredentialRecord] = []
    user = repo.get_user_by_email(email) if email else None
    if user:
        creds = repo.list_webauthn_credentials(user.oid)

    if creds:
        allow = [
            PublicKeyCredentialDescriptor(
                id=base64url_to_bytes(c.credential_id),
                transports=[AuthenticatorTransport(t) for t in c.transports if t] or None,
            )
            for c in creds
        ]
    else:
        # Decoy so unknown/passkey-less emails are indistinguishable.
        allow = [PublicKeyCredentialDescriptor(id=_decoy_credential_id(email))]

    options = generate_authentication_options(
        rp_id=settings.webauthn_rp_id,
        allow_credentials=allow,
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    session["pk_auth_chal"] = bytes_to_base64url(options.challenge)
    return Response(options_to_json(options), mimetype="application/json")


@passkey_bp.route("/login/complete", methods=["POST"])
def login_complete() -> ResponseReturnValue:
    settings = get_settings()
    if not settings.passkey_enabled:
        return jsonify({"error": "Passkeys are not enabled."}), 404
    repo = get_repository()

    chal = session.pop("pk_auth_chal", None)
    if not chal:
        return jsonify({"error": _GENERIC_LOGIN_ERR}), 401

    raw = request.get_data(as_text=True)
    try:
        cred_id = json.loads(raw).get("id")
    except Exception:
        return jsonify({"error": _GENERIC_LOGIN_ERR}), 401

    stored = repo.get_webauthn_credential(cred_id) if cred_id else None
    if stored is None:
        # Unknown credential — covers decoy and any tampering, uniformly.
        return jsonify({"error": _GENERIC_LOGIN_ERR}), 401

    try:
        verification = verify_authentication_response(
            credential=raw,
            expected_challenge=base64url_to_bytes(chal),
            expected_rp_id=settings.webauthn_rp_id,
            expected_origin=settings.webauthn_origin,
            credential_public_key=base64url_to_bytes(stored.public_key),
            credential_current_sign_count=stored.sign_count,
            require_user_verification=False,
        )
    except Exception:
        logger.warning("Passkey authentication verification failed", exc_info=True)
        return jsonify({"error": _GENERIC_LOGIN_ERR}), 401

    repo.update_webauthn_sign_count(stored.credential_id, verification.new_sign_count)
    user = repo.get_user(stored.user_oid)
    if user is None:
        return jsonify({"error": _GENERIC_LOGIN_ERR}), 401

    _complete_login(user.oid, user.name, user.email)
    return jsonify({"ok": True, "redirect": _safe_redirect_url(session.pop("next_url", None))})


# ---------------------------------------------------------------------------
# Manage passkeys (authenticated)
# ---------------------------------------------------------------------------


@passkey_bp.route("/list", methods=["GET"])
def list_passkeys() -> ResponseReturnValue:
    current = _session_user()
    if not current:
        return jsonify({"error": "Not signed in."}), 401
    repo = get_repository()
    creds = repo.list_webauthn_credentials(current["oid"])
    return jsonify(
        [
            {
                "id": c.credential_id,
                "nickname": c.nickname or "Passkey",
                "created_at": c.created_at.isoformat() if c.created_at else None,
                "last_used_at": c.last_used_at.isoformat() if c.last_used_at else None,
            }
            for c in creds
        ]
    )


@passkey_bp.route("/delete", methods=["POST"])
def delete_passkey() -> ResponseReturnValue:
    current = _session_user()
    if not current:
        return jsonify({"error": "Not signed in."}), 401
    cred_id = (request.get_json(silent=True) or {}).get("credential_id")
    if not cred_id:
        return jsonify({"error": "Missing credential id."}), 400
    get_repository().delete_webauthn_credential(cred_id, current["oid"])
    return jsonify({"ok": True})
