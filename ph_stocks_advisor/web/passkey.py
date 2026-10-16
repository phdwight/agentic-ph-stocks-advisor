"""
Passkey (WebAuthn / FIDO2) sign-in blueprint.

Model: **passkey-first, open self-signup, email-first login.** Google/MS
OAuth remains available (via ``auth.py``) as an account-recovery fallback.

Ceremonies use py_webauthn. Registration/authentication challenges are held
in the Flask session between the two round-trips. Verification is always done
against the *configured* origin/RP ID (``settings.webauthn_*``), never values
inferred from the request — safe behind the cloudflared tunnel.

Emailed one-time code: when the typed email has no passkey (an OAuth-only
account, an account whose passkeys were removed, or no account at all),
``login/begin`` emails a 6-digit code instead of starting a WebAuthn
ceremony, and ``login/verify-code`` signs the user in (creating the account,
with disclaimer consent, if it doesn't exist). Accounts that DO have a
passkey can only sign in with it.

Enumeration trade-off (deliberate, product decision): because ``login/begin``
answers differently for "has a passkey" vs "gets a code" (and flags new
accounts so the UI can collect consent), it reveals whether an email is
registered and whether it has a passkey. Probing for a code-path email costs
a real email send and is throttled per session by the resend cooldown.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
import uuid
from collections.abc import Callable
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

from ph_stocks_advisor.infra.config import get_email_sender, get_repository, get_settings
from ph_stocks_advisor.infra.email import (
    EmailSendError,
    build_login_code_email,
    build_verification_email,
    email_failure_is_quota,
)
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

# Email verification: a 6-digit code emailed at sign-up, required before the
# WebAuthn ceremony may start. One message for missing/wrong/expired/exhausted
# so ``register/begin`` stays uniform whether or not the email is registered
# (the exists-check lives in ``send-code``, where probing costs a real email
# send and is throttled by the cooldown).
_BAD_CODE_ERR = "That verification code is incorrect or has expired. Request a new one."
_CODE_COOLDOWN_ERR = "A code was just sent to that email. Wait a minute before requesting another."
_CODE_SEND_ERR = "Couldn't send the verification code right now. Try again in a moment."
# The provider ran out of send credit/quota — retrying won't help, so say so
# plainly and point at the operator instead of the user.
_CODE_UNAVAILABLE_ERR = (
    "Sign-up email is temporarily unavailable. Please try again later or contact the site administrator."
)
_LOGIN_CODE_UNAVAILABLE_ERR = (
    "Sign-in email is temporarily unavailable. Please try again later or contact the site administrator."
)
_LOGIN_CODE_SEND_ERR = "Couldn't send the sign-in code right now. Try again in a moment."
# Emailed codes are a fallback for accounts WITHOUT a passkey; an account that
# has one must use it (otherwise the inbox becomes the weakest link).
_HAS_PASSKEY_ERR = "This account uses a passkey. Sign in with your passkey instead."
_CODE_TTL_SECONDS = 10 * 60
_CODE_MAX_ATTEMPTS = 5
_CODE_RESEND_COOLDOWN_SECONDS = 60
# The newest N codes stay valid (each with its own TTL). A resend must not
# retire the code already sitting in the user's inbox: the emails look
# identical, delivery is not instant, and the user cannot tell which message
# holds the "current" code — with only one valid at a time, opening the older
# email makes its code look mysteriously used up.
_CODE_KEEP = 2

# Session-key prefixes for the two independent code flows, so a pending
# sign-up code and a pending sign-in code never overwrite each other.
_SIGNUP_CODE = "pk_vc"
_LOGIN_CODE = "pk_lc"

# This regex only rejects malformed input (local@domain.tld, no spaces, a dot
# in the domain); deliverability is proven by the verification code itself.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _valid_email(email: str) -> bool:
    return bool(email) and len(email) <= 254 and _EMAIL_RE.match(email) is not None


def _user_handle(oid: str) -> bytes:
    """Stable, opaque 16-byte WebAuthn user handle derived from the account oid."""
    return hashlib.sha256(f"wauh:{oid}".encode()).digest()[:16]


def _session_user() -> dict | None:
    """The genuinely authenticated user (ignores the auth-disabled dev user)."""
    return session.get("user")


def _complete_login(oid: str, name: str, email: str, provider: str = "passkey") -> None:
    """Set the session user exactly like the OAuth flow, incl. DB user_type.

    *provider* records HOW this session signed in (``passkey`` or ``email``
    for a one-time code) — not the account's original provider.
    """
    repo = get_repository()
    session["user"] = {
        "name": name,
        "email": email,
        "oid": oid,
        "provider": provider,
        "user_type": 0,
    }
    db_user = repo.get_user(oid)
    if db_user:
        session["user"]["user_type"] = db_user.user_type
    logger.info("%s sign-in: %s", provider.capitalize(), email)


def _transports_from(raw_json: str) -> list[str]:
    try:
        return list(json.loads(raw_json).get("response", {}).get("transports", []) or [])
    except Exception:
        return []


def _login_transports(stored: list[str]) -> list[AuthenticatorTransport] | None:
    """Stored transports plus ``hybrid``, so a passkey held on another device
    can still be offered via the cross-device (QR/Bluetooth) sign-in flow."""
    names = list(dict.fromkeys([*(t for t in stored if t), "hybrid"]))
    out: list[AuthenticatorTransport] = []
    for name in names:
        try:
            out.append(AuthenticatorTransport(name))
        except ValueError:
            continue
    return out or None


# ---------------------------------------------------------------------------
# Emailed codes (sign-up verification and passkey-less sign-in)
# ---------------------------------------------------------------------------


def _code_hash(code: str, email: str) -> str:
    """HMAC the code (keyed by the app secret, bound to the email) so the
    session never holds the plaintext code."""
    secret = get_settings().flask_secret_key.encode()
    return hmac.new(secret, f"vcode:{email}:{code}".encode(), hashlib.sha256).hexdigest()


def _now_ts() -> float:
    return datetime.now(tz=UTC).timestamp()


def _in_cooldown(prefix: str) -> bool:
    return _now_ts() - (session.get(f"{prefix}_sent_at") or 0) < _CODE_RESEND_COOLDOWN_SECONDS


def _send_code(
    prefix: str,
    email: str,
    *,
    build: Callable[..., tuple[str, str]],
    send_err: str,
    unavailable_err: str,
) -> ResponseReturnValue | None:
    """Generate, email and store a fresh code under *prefix*.

    Returns ``None`` on success, or the error response to hand back. Session
    state is only touched after a successful send, so a failed send neither
    leaves a code behind nor starts the cooldown.
    """
    code = f"{secrets.randbelow(1_000_000):06d}"
    try:
        subject, html = build(code=code, expires_minutes=_CODE_TTL_SECONDS // 60)
        get_email_sender().send(to=email, subject=subject, html=html)
    except EmailSendError as exc:
        # Provider quota/credit exhausted retries can't fix this, so tell the
        # user plainly and log the real cause (not a generic "send failed").
        if email_failure_is_quota(exc):
            logger.error("Email code blocked — email provider quota/credit exhausted (to=%s): %s", email, exc)
            return jsonify({"error": unavailable_err}), 503
        logger.error("Failed to send email code to %s: %s", email, exc, exc_info=True)
        return jsonify({"error": send_err}), 502
    except Exception:
        # An unexpected bug in the mail path — never a raw 500 to the user, and
        # the traceback names the real fault in the log.
        logger.error("Unexpected error sending email code to %s", email, exc_info=True)
        return jsonify({"error": send_err}), 502

    # Keep earlier still-live codes for the same email so the user can enter
    # whichever email they end up reading; a different email starts fresh
    # (old hashes are bound to the old address and could never match anyway).
    now = _now_ts()
    codes = session.get(f"{prefix}_codes") or []
    if session.get(f"{prefix}_email") != email:
        codes = []
    codes = [c for c in codes if now <= c["e"]]
    codes.append({"h": _code_hash(code, email), "e": now + _CODE_TTL_SECONDS})
    session[f"{prefix}_codes"] = codes[-_CODE_KEEP:]
    session[f"{prefix}_email"] = email
    session[f"{prefix}_attempts"] = 0
    session[f"{prefix}_sent_at"] = now
    return None


def _verify_code(prefix: str, email: str, code: str) -> bool:
    """Check *code* against the session state under *prefix*; wrong guesses
    burn an attempt (``_CODE_MAX_ATTEMPTS`` per issued batch)."""
    codes = session.get(f"{prefix}_codes") or []
    sent_email = session.get(f"{prefix}_email")
    attempts = session.get(f"{prefix}_attempts") or 0

    if not codes or sent_email != email:
        return False
    if attempts >= _CODE_MAX_ATTEMPTS:
        return False
    now = _now_ts()
    live = [c["h"] for c in codes if now <= c["e"]]
    if not live:
        return False
    if code:
        guess = _code_hash(code, email)
        # Compare against every live hash (no early exit) to keep timing flat.
        if sum(hmac.compare_digest(guess, h) for h in live) > 0:
            return True
    session[f"{prefix}_attempts"] = attempts + 1
    return False


def _clear_code_state(prefix: str) -> None:
    for suffix in ("codes", "email", "attempts", "sent_at"):
        session.pop(f"{prefix}_{suffix}", None)


def _verify_signup_code(email: str, code: str) -> bool:
    """Check *code* against the session state; wrong guesses burn an attempt.

    Deliberately NOT single-use: a dismissed/timed-out passkey prompt makes the
    browser re-run ``register/begin``, and forcing a fresh code there would
    punish the common retry. The newest ``_CODE_KEEP`` codes are all accepted
    (each within its own TTL) so a resend doesn't strand the email the user is
    reading. The state is session-bound and short-lived; it is cleared when
    registration actually completes (``register/complete``).
    """
    return _verify_code(_SIGNUP_CODE, email, code)


def _clear_signup_code_state() -> None:
    _clear_code_state(_SIGNUP_CODE)


@passkey_bp.route("/register/send-code", methods=["POST"])
def register_send_code() -> ResponseReturnValue:
    """Email a verification code for a new-account registration.

    Runs the same gates as ``register/begin`` (valid email, consent, email not
    already registered) so a user can't pass this step and then trip over
    those errors after typing the code.
    """
    settings = get_settings()
    if not settings.passkey_enabled:
        return jsonify({"error": "Passkeys are not enabled."}), 404
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    if not _valid_email(email):
        return jsonify({"error": _INVALID_EMAIL_ERR}), 400
    if data.get("accept_disclaimer") is not True:
        return jsonify({"error": _NO_CONSENT_ERR}), 400
    if get_repository().get_user_by_email(email) is not None:
        return jsonify({"error": _GENERIC_REG_ERR}), 400

    if _in_cooldown(_SIGNUP_CODE):
        return jsonify({"error": _CODE_COOLDOWN_ERR}), 429

    err = _send_code(
        _SIGNUP_CODE,
        email,
        build=build_verification_email,
        send_err=_CODE_SEND_ERR,
        unavailable_err=_CODE_UNAVAILABLE_ERR,
    )
    return err or jsonify({"ok": True})


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
        # The emailed code proves the address is real and reachable. Checked
        # BEFORE the exists-check so this endpoint answers uniformly whether
        # or not the email is registered (enumeration lives only behind
        # send-code, where probing costs an actual email + cooldown).
        if not _verify_signup_code(email, str(data.get("code") or "")):
            return jsonify({"error": _BAD_CODE_ERR}), 400
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
            # PREFERRED makes this a discoverable passkey, so it syncs across
            # the user's devices (iCloud Keychain / Google Password Manager)
            # and can be used to sign in from another device. DISCOURAGED
            # would bind it to one authenticator and block cross-device login.
            resident_key=ResidentKeyRequirement.PREFERRED,
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
        # The email was proven reachable at register/begin — retire the code.
        _clear_signup_code_state()
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


def _passkeys_for(email: str) -> tuple[UserRecord | None, list[WebAuthnCredentialRecord]]:
    user = get_repository().get_user_by_email(email)
    return user, (get_repository().list_webauthn_credentials(user.oid) if user else [])


@passkey_bp.route("/login/begin", methods=["POST"])
def login_begin() -> ResponseReturnValue:
    """Start sign-in for the typed email.

    Has a passkey → WebAuthn authentication options (as before).
    No passkey / no account → email a one-time code and answer
    ``{"otp": true, "new_account": bool, "sent": bool}`` so the UI switches
    to the code field (and, for a new account, the name + consent fields).
    """
    settings = get_settings()
    if not settings.passkey_enabled:
        return jsonify({"error": "Passkeys are not enabled."}), 404
    email = ((request.get_json(silent=True) or {}).get("email") or "").strip().lower()
    if not _valid_email(email):
        return jsonify({"error": _INVALID_EMAIL_ERR}), 400

    user, creds = _passkeys_for(email)
    if not creds:
        body = {"otp": True, "email": email, "new_account": user is None}
        if _in_cooldown(_LOGIN_CODE):
            # A code went to this same address moments ago (double submit,
            # back-and-forth) — it's still valid, so reuse it, don't re-send.
            if session.get(f"{_LOGIN_CODE}_email") == email:
                return jsonify({**body, "sent": False})
            return jsonify({"error": _CODE_COOLDOWN_ERR}), 429
        err = _send_code(
            _LOGIN_CODE,
            email,
            build=build_login_code_email,
            send_err=_LOGIN_CODE_SEND_ERR,
            unavailable_err=_LOGIN_CODE_UNAVAILABLE_ERR,
        )
        return err or jsonify({**body, "sent": True})

    allow = [
        PublicKeyCredentialDescriptor(
            id=base64url_to_bytes(c.credential_id),
            # Always offer "hybrid" alongside the stored transports so the
            # browser surfaces the cross-device (phone → this device via
            # QR/Bluetooth) flow when the passkey lives on another device.
            transports=_login_transports(c.transports),
        )
        for c in creds
    ]

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


@passkey_bp.route("/login/send-code", methods=["POST"])
def login_send_code() -> ResponseReturnValue:
    """Resend the one-time sign-in code (explicit user action → cooldown 429)."""
    settings = get_settings()
    if not settings.passkey_enabled:
        return jsonify({"error": "Passkeys are not enabled."}), 404
    email = ((request.get_json(silent=True) or {}).get("email") or "").strip().lower()
    if not _valid_email(email):
        return jsonify({"error": _INVALID_EMAIL_ERR}), 400
    _user, creds = _passkeys_for(email)
    if creds:
        return jsonify({"error": _HAS_PASSKEY_ERR, "use_passkey": True}), 400
    if _in_cooldown(_LOGIN_CODE):
        return jsonify({"error": _CODE_COOLDOWN_ERR}), 429
    err = _send_code(
        _LOGIN_CODE,
        email,
        build=build_login_code_email,
        send_err=_LOGIN_CODE_SEND_ERR,
        unavailable_err=_LOGIN_CODE_UNAVAILABLE_ERR,
    )
    return err or jsonify({"ok": True})


@passkey_bp.route("/login/verify-code", methods=["POST"])
def login_verify_code() -> ResponseReturnValue:
    """Sign in with the emailed one-time code.

    Existing account → signed in. Unknown email → a new account is created,
    which (like passkey sign-up) requires ``accept_disclaimer: true``; the
    consent is checked BEFORE the code so a missing tick doesn't burn an
    attempt. Single-use: the code state is cleared on success.
    """
    settings = get_settings()
    if not settings.passkey_enabled:
        return jsonify({"error": "Passkeys are not enabled."}), 404
    repo = get_repository()
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    if not _valid_email(email):
        return jsonify({"error": _INVALID_EMAIL_ERR}), 400

    user, creds = _passkeys_for(email)
    if user is None and data.get("accept_disclaimer") is not True:
        return jsonify({"error": _NO_CONSENT_ERR}), 400
    if not _verify_code(_LOGIN_CODE, email, str(data.get("code") or "").strip()):
        return jsonify({"error": _BAD_CODE_ERR}), 400
    if creds:
        # A passkey was added after the code went out — passkey accounts
        # never sign in by email code.
        _clear_code_state(_LOGIN_CODE)
        return jsonify({"error": _HAS_PASSKEY_ERR, "use_passkey": True}), 400

    _clear_code_state(_LOGIN_CODE)
    if user is None:
        user = UserRecord(
            oid=f"email:{uuid.uuid4().hex}",
            name=(data.get("name") or "").strip()[:200] or email,
            email=email,
            provider="email",
            disclaimer_version=_DISCLAIMER_VERSION,
            disclaimer_accepted_at=datetime.now(tz=UTC),
        )
        repo.save_user(user)

    _complete_login(user.oid, user.name, user.email, provider="email")
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
