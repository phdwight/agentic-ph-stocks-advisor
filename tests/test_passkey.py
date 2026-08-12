"""Tests for passkey (WebAuthn) sign-in.

Covers repository CRUD and route *behaviour* — anti-enumeration uniformity,
the account-takeover guard, auth-required endpoints, and the disabled path.
The full cryptographic register→login happy path is exercised end-to-end in
Docker via a Playwright CDP virtual authenticator (not unit-testable without
a software authenticator).
"""

from __future__ import annotations

import pytest

from ph_stocks_advisor.web.app import create_app

# ---------------------------------------------------------------------------
# Repository CRUD (backend-agnostic contract, exercised on SQLite)
# ---------------------------------------------------------------------------


def test_repo_webauthn_crud():
    from ph_stocks_advisor.infra.repository import WebAuthnCredentialRecord
    from ph_stocks_advisor.infra.repository_sqlite import SQLiteReportRepository

    repo = SQLiteReportRepository(":memory:")
    repo.initialize()
    repo.add_webauthn_credential(
        WebAuthnCredentialRecord(
            credential_id="cred-a",
            user_oid="passkey:u",
            public_key="pk",
            transports=["internal", "hybrid"],
            nickname="Laptop",
        )
    )
    got = repo.get_webauthn_credential("cred-a")
    assert got is not None
    assert got.user_oid == "passkey:u"
    assert got.transports == ["internal", "hybrid"]
    assert got.nickname == "Laptop"

    repo.update_webauthn_sign_count("cred-a", 9)
    got = repo.get_webauthn_credential("cred-a")
    assert got is not None
    assert got.sign_count == 9 and got.last_used_at is not None

    assert len(repo.list_webauthn_credentials("passkey:u")) == 1
    # Deletion is scoped to the owner — wrong owner is a no-op.
    repo.delete_webauthn_credential("cred-a", "passkey:someone-else")
    assert repo.get_webauthn_credential("cred-a") is not None
    repo.delete_webauthn_credential("cred-a", "passkey:u")
    assert repo.get_webauthn_credential("cred-a") is None


# ---------------------------------------------------------------------------
# Route fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def passkey_app(monkeypatch, tmp_path):
    # A per-test file DB avoids the shared-cache ":memory:" schema leaking
    # between tests in the same process.
    db_path = str(tmp_path / "passkey.db")
    monkeypatch.setenv("DB_BACKEND", "sqlite")
    monkeypatch.setenv("SQLITE_PATH", db_path)
    monkeypatch.setenv("FLASK_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("WEBAUTHN_RP_ID", "localhost")
    monkeypatch.setenv("WEBAUTHN_ORIGIN", "http://localhost:5180")
    monkeypatch.delenv("ENTRA_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)

    import ph_stocks_advisor.infra.config as cfg

    cfg._reset_repository()
    cfg.get_settings.cache_clear()
    s = cfg.get_settings()
    s.db_backend = "sqlite"
    s.sqlite_path = db_path
    s.entra_client_id = ""
    s.google_client_id = ""
    s.webauthn_rp_id = "localhost"
    s.webauthn_origin = "http://localhost:5180"
    s.flask_secret_key = "test-secret-key"

    yield create_app()

    cfg._reset_repository()
    cfg.get_settings.cache_clear()


@pytest.fixture
def pk_client(passkey_app):
    return passkey_app.test_client()


def _csrf(client) -> dict[str, str]:
    """Seed a CSRF token into the session and return the matching header."""
    with client.session_transaction() as sess:
        sess["_csrf_token"] = "tok"
    return {"X-CSRFToken": "tok"}


def _seed_user_with_credential(email: str, oid: str = "passkey:seed") -> None:
    from webauthn.helpers import bytes_to_base64url

    from ph_stocks_advisor.infra.config import get_repository
    from ph_stocks_advisor.infra.repository import UserRecord, WebAuthnCredentialRecord

    repo = get_repository()
    repo.save_user(UserRecord(oid=oid, name="Seed", email=email, provider="passkey"))
    repo.add_webauthn_credential(
        WebAuthnCredentialRecord(
            credential_id=bytes_to_base64url(b"seed-credential-id"),
            user_oid=oid,
            public_key=bytes_to_base64url(b"seed-public-key"),
        )
    )


class RecordingSender:
    """Captures outgoing mail so tests can read the verification code."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    def send(self, *, to: str, subject: str, html: str) -> None:
        self.sent.append((to, subject, html))


@pytest.fixture
def mail(monkeypatch) -> RecordingSender:
    sender = RecordingSender()
    monkeypatch.setattr("ph_stocks_advisor.web.passkey.get_email_sender", lambda: sender)
    return sender


def _request_code(client, mail: RecordingSender, email: str, hdr: dict[str, str]) -> str:
    """Go through send-code and return the 6-digit code that was emailed.

    Clears the per-session resend cooldown first so tests can request codes
    back-to-back.
    """
    import re

    with client.session_transaction() as sess:
        sess.pop("pk_vc_sent_at", None)
    resp = client.post(
        "/auth/passkey/register/send-code",
        json={"email": email, "accept_disclaimer": True},
        headers=hdr,
    )
    assert resp.status_code == 200, resp.get_json()
    to, _subject, html = mail.sent[-1]
    assert to == email
    match = re.search(r"\b(\d{6})\b", html)
    assert match, "verification email must contain a 6-digit code"
    return match.group(1)


# ---------------------------------------------------------------------------
# Behaviour
# ---------------------------------------------------------------------------


def test_disabled_returns_404(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_BACKEND", "sqlite")
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "off.db"))
    monkeypatch.setenv("WEBAUTHN_RP_ID", "")
    monkeypatch.delenv("ENTRA_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)

    import ph_stocks_advisor.infra.config as cfg

    cfg._reset_repository()
    cfg.get_settings.cache_clear()
    s = cfg.get_settings()
    s.db_backend = "sqlite"
    s.sqlite_path = str(tmp_path / "off.db")
    s.webauthn_rp_id = ""
    s.entra_client_id = ""
    s.google_client_id = ""
    client = create_app().test_client()

    resp = client.post("/auth/passkey/login/begin", json={"email": "x@y.com"})
    assert resp.status_code == 404

    cfg._reset_repository()
    cfg.get_settings.cache_clear()


def test_login_begin_returns_decoy_options(pk_client):
    hdr = _csrf(pk_client)
    resp = pk_client.post("/auth/passkey/login/begin", json={"email": "nobody@example.com"}, headers=hdr)
    assert resp.status_code == 200
    data = resp.get_json()
    assert "challenge" in data
    assert data["allowCredentials"], "unknown email must still get a (decoy) allowCredentials"

    # Decoy is deterministic per email so repeated probes can't distinguish it.
    again = pk_client.post("/auth/passkey/login/begin", json={"email": "nobody@example.com"}, headers=hdr)
    assert again.get_json()["allowCredentials"][0]["id"] == data["allowCredentials"][0]["id"]


def test_login_begin_known_and_unknown_are_indistinguishable(pk_client):
    _seed_user_with_credential("known@example.com")
    hdr = _csrf(pk_client)
    known = pk_client.post("/auth/passkey/login/begin", json={"email": "known@example.com"}, headers=hdr).get_json()
    unknown = pk_client.post("/auth/passkey/login/begin", json={"email": "ghost@example.com"}, headers=hdr).get_json()
    # Same response shape; both non-empty — nothing reveals which email exists.
    assert set(known.keys()) == set(unknown.keys())
    assert known["allowCredentials"] and unknown["allowCredentials"]


def test_login_complete_unknown_credential_is_generic_401(pk_client):
    hdr = _csrf(pk_client)
    pk_client.post("/auth/passkey/login/begin", json={"email": "nobody@example.com"}, headers=hdr)
    resp = pk_client.post(
        "/auth/passkey/login/complete",
        json={"id": "bm90LXJlYWw", "type": "public-key", "response": {}},
        headers=hdr,
    )
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "Couldn't sign you in with a passkey. Check the email and try again."


def test_register_begin_new_email_with_code_returns_options(pk_client, mail):
    hdr = _csrf(pk_client)
    code = _request_code(pk_client, mail, "new@example.com", hdr)
    resp = pk_client.post(
        "/auth/passkey/register/begin",
        json={"email": "new@example.com", "name": "New User", "accept_disclaimer": True, "code": code},
        headers=hdr,
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert "challenge" in data
    assert data["user"]["id"]
    assert data["rp"]["id"] == "localhost"


def test_send_code_existing_email_is_generic(pk_client, mail):
    """The exists-check lives at send-code; probing costs an email + cooldown."""
    _seed_user_with_credential("taken@example.com")
    hdr = _csrf(pk_client)
    resp = pk_client.post(
        "/auth/passkey/register/send-code",
        json={"email": "taken@example.com", "accept_disclaimer": True},
        headers=hdr,
    )
    assert resp.status_code == 400
    # The generic message must NOT reveal that the email is already registered.
    assert (
        resp.get_json()["error"]
        == "Couldn't set up a passkey for that email. If you already have an account, sign in instead."
    )
    assert mail.sent == []


def test_register_begin_without_code_is_uniform(pk_client, mail):
    """No valid code → the same error whether or not the email is registered,
    so ``register/begin`` itself leaks nothing."""
    _seed_user_with_credential("taken@example.com")
    hdr = _csrf(pk_client)
    responses = []
    for email in ("taken@example.com", "ghost@example.com"):
        resp = pk_client.post(
            "/auth/passkey/register/begin",
            json={"email": email, "name": "X", "accept_disclaimer": True, "code": "123456"},
            headers=hdr,
        )
        responses.append((resp.status_code, resp.get_json()["error"]))
    expected = (400, "That verification code is incorrect or has expired. Request a new one.")
    assert responses[0] == responses[1] == expected


def test_manage_endpoints_require_auth(pk_client):
    assert pk_client.get("/auth/passkey/list").status_code == 401
    hdr = _csrf(pk_client)
    assert pk_client.post("/auth/passkey/delete", json={"credential_id": "x"}, headers=hdr).status_code == 401


@pytest.mark.parametrize(
    "bad_email",
    ["", "nope", "a@b", "a b@c.com", "@example.com", "x@", "x@y.", "two@@at.com"],
)
def test_register_and_login_reject_malformed_email(pk_client, bad_email):
    hdr = _csrf(pk_client)
    for url in ("/auth/passkey/register/begin", "/auth/passkey/register/send-code"):
        reg = pk_client.post(url, json={"email": bad_email, "name": "X", "accept_disclaimer": True}, headers=hdr)
        assert reg.status_code == 400, url
        assert reg.get_json()["error"] == "Enter a valid email address."

    login = pk_client.post("/auth/passkey/login/begin", json={"email": bad_email}, headers=hdr)
    assert login.status_code == 400
    assert login.get_json()["error"] == "Enter a valid email address."


def test_valid_email_forms_are_accepted(pk_client, mail):
    hdr = _csrf(pk_client)
    for ok in ["a@b.co", "first.last@sub.example.com", "user+tag@example.io"]:
        code = _request_code(pk_client, mail, ok, hdr)
        reg = pk_client.post(
            "/auth/passkey/register/begin",
            json={"email": ok, "name": "X", "accept_disclaimer": True, "code": code},
            headers=hdr,
        )
        assert reg.status_code == 200, ok


# ---------------------------------------------------------------------------
# Sign-up consent — the disclaimer must be accepted to create an account
# ---------------------------------------------------------------------------


def test_registration_requires_disclaimer_consent(pk_client):
    """The checkbox in the UI is convenience; the server is the real gate."""
    hdr = _csrf(pk_client)
    resp = pk_client.post(
        "/auth/passkey/register/begin",
        json={"email": "newuser@example.com", "name": "New User"},  # no consent
        headers=hdr,
    )
    assert resp.status_code == 400
    assert "accept" in resp.get_json()["error"].lower()


@pytest.mark.parametrize("value", [False, "true", 1, None, "yes"])
def test_consent_must_be_boolean_true(pk_client, value):
    """Only an explicit boolean True counts — no truthy coercion."""
    hdr = _csrf(pk_client)
    resp = pk_client.post(
        "/auth/passkey/register/begin",
        json={"email": "newuser@example.com", "name": "N", "accept_disclaimer": value},
        headers=hdr,
    )
    assert resp.status_code == 400


def test_adding_a_device_to_an_existing_account_needs_no_consent(pk_client):
    """An authenticated user adding a second passkey already accepted the
    terms at sign-up — don't re-gate them."""
    _seed_user_with_credential("member@example.com", oid="passkey:member")
    with pk_client.session_transaction() as sess:
        sess["user"] = {
            "name": "Member",
            "email": "member@example.com",
            "oid": "passkey:member",
            "provider": "passkey",
            "user_type": 0,
        }
    resp = pk_client.post("/auth/passkey/register/begin", json={}, headers=_csrf(pk_client))
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Email verification — the code emailed at sign-up
# ---------------------------------------------------------------------------


def test_send_code_requires_consent(pk_client, mail):
    hdr = _csrf(pk_client)
    resp = pk_client.post("/auth/passkey/register/send-code", json={"email": "new@example.com"}, headers=hdr)
    assert resp.status_code == 400
    assert "accept" in resp.get_json()["error"].lower()
    assert mail.sent == []


def test_code_is_bound_to_the_email_it_was_sent_to(pk_client, mail):
    hdr = _csrf(pk_client)
    code = _request_code(pk_client, mail, "alice@example.com", hdr)
    resp = pk_client.post(
        "/auth/passkey/register/begin",
        json={"email": "mallory@example.com", "name": "M", "accept_disclaimer": True, "code": code},
        headers=hdr,
    )
    assert resp.status_code == 400


def test_wrong_code_burns_attempts_until_lockout(pk_client, mail):
    hdr = _csrf(pk_client)
    code = _request_code(pk_client, mail, "new@example.com", hdr)
    wrong = "000000" if code != "000000" else "111111"
    payload = {"email": "new@example.com", "name": "N", "accept_disclaimer": True}

    for _ in range(5):
        resp = pk_client.post("/auth/passkey/register/begin", json={**payload, "code": wrong}, headers=hdr)
        assert resp.status_code == 400
    # Attempts exhausted — even the REAL code is now refused.
    resp = pk_client.post("/auth/passkey/register/begin", json={**payload, "code": code}, headers=hdr)
    assert resp.status_code == 400


def test_expired_code_is_rejected(pk_client, mail):
    hdr = _csrf(pk_client)
    code = _request_code(pk_client, mail, "new@example.com", hdr)
    with pk_client.session_transaction() as sess:
        sess["pk_vc_codes"] = [{**c, "e": 1.0} for c in sess["pk_vc_codes"]]  # long past
    resp = pk_client.post(
        "/auth/passkey/register/begin",
        json={"email": "new@example.com", "name": "N", "accept_disclaimer": True, "code": code},
        headers=hdr,
    )
    assert resp.status_code == 400


def test_resend_keeps_the_previous_code_valid(pk_client, mail):
    """A resend must not retire the code already in the user's inbox — the
    emails look identical and the newest may not have arrived yet, so the
    user may well type the older one. The newest two codes both work."""
    hdr = _csrf(pk_client)
    code_a = _request_code(pk_client, mail, "new@example.com", hdr)
    code_b = _request_code(pk_client, mail, "new@example.com", hdr)
    assert code_a != code_b
    payload = {"email": "new@example.com", "name": "N", "accept_disclaimer": True}
    assert (
        pk_client.post(
            "/auth/passkey/register/begin", json={**payload, "code": code_a}, headers=hdr
        ).status_code
        == 200
    )
    assert (
        pk_client.post(
            "/auth/passkey/register/begin", json={**payload, "code": code_b}, headers=hdr
        ).status_code
        == 200
    )


def test_only_the_newest_two_codes_survive(pk_client, mail):
    hdr = _csrf(pk_client)
    code_a = _request_code(pk_client, mail, "new@example.com", hdr)
    _request_code(pk_client, mail, "new@example.com", hdr)
    code_c = _request_code(pk_client, mail, "new@example.com", hdr)
    payload = {"email": "new@example.com", "name": "N", "accept_disclaimer": True}
    resp = pk_client.post(
        "/auth/passkey/register/begin", json={**payload, "code": code_a}, headers=hdr
    )
    assert resp.status_code == 400  # pushed out by the two later sends
    assert (
        pk_client.post(
            "/auth/passkey/register/begin", json={**payload, "code": code_c}, headers=hdr
        ).status_code
        == 200
    )


def test_a_different_email_starts_the_code_state_fresh(pk_client, mail):
    hdr = _csrf(pk_client)
    code_a = _request_code(pk_client, mail, "first@example.com", hdr)
    _request_code(pk_client, mail, "second@example.com", hdr)
    resp = pk_client.post(
        "/auth/passkey/register/begin",
        json={"email": "first@example.com", "name": "N", "accept_disclaimer": True, "code": code_a},
        headers=hdr,
    )
    assert resp.status_code == 400


def test_code_survives_a_retried_ceremony(pk_client, mail):
    """A dismissed passkey prompt re-runs register/begin — the same code must
    still work (it is retired only when registration completes)."""
    hdr = _csrf(pk_client)
    code = _request_code(pk_client, mail, "new@example.com", hdr)
    payload = {"email": "new@example.com", "name": "N", "accept_disclaimer": True, "code": code}
    assert pk_client.post("/auth/passkey/register/begin", json=payload, headers=hdr).status_code == 200
    assert pk_client.post("/auth/passkey/register/begin", json=payload, headers=hdr).status_code == 200


def test_resend_is_throttled(pk_client, mail):
    hdr = _csrf(pk_client)
    _request_code(pk_client, mail, "new@example.com", hdr)  # clears + sets cooldown
    resp = pk_client.post(
        "/auth/passkey/register/send-code",
        json={"email": "new@example.com", "accept_disclaimer": True},
        headers=hdr,
    )
    assert resp.status_code == 429
    assert len(mail.sent) == 1


def test_send_failure_is_a_502_and_leaves_no_code_state(pk_client, monkeypatch):
    from ph_stocks_advisor.infra.email import EmailSendError

    class BrokenSender:
        def send(self, **_kw) -> None:
            raise EmailSendError("provider down")

    monkeypatch.setattr("ph_stocks_advisor.web.passkey.get_email_sender", lambda: BrokenSender())
    hdr = _csrf(pk_client)
    resp = pk_client.post(
        "/auth/passkey/register/send-code",
        json={"email": "new@example.com", "accept_disclaimer": True},
        headers=hdr,
    )
    assert resp.status_code == 502
    with pk_client.session_transaction() as sess:
        assert "pk_vc_codes" not in sess
        assert "pk_vc_sent_at" not in sess  # a failed send must not start the cooldown


def test_unexpected_send_error_is_also_a_502_not_a_500(pk_client, monkeypatch):
    """Any exception in the mail path — not just EmailSendError — must come
    back as the same actionable 502, never an unhandled 500."""

    class BuggySender:
        def send(self, **_kw) -> None:
            raise TypeError("unexpected bug")

    monkeypatch.setattr("ph_stocks_advisor.web.passkey.get_email_sender", lambda: BuggySender())
    hdr = _csrf(pk_client)
    resp = pk_client.post(
        "/auth/passkey/register/send-code",
        json={"email": "new@example.com", "accept_disclaimer": True},
        headers=hdr,
    )
    assert resp.status_code == 502
    assert "verification code" in resp.get_json()["error"]
    with pk_client.session_transaction() as sess:
        assert "pk_vc_codes" not in sess


def test_login_page_has_the_code_step_ui(pk_client):
    html = pk_client.get("/auth/login").get_data(as_text=True)
    assert 'id="pk-code"' in html
    assert 'id="pk-resend"' in html


def test_login_page_shows_the_full_disclaimer_and_consent_box(pk_client):
    """The full terms must be present on the sign-up page itself — not just a
    link — and the acceptance checkbox must exist."""
    html = pk_client.get("/auth/login").get_data(as_text=True)
    assert 'id="pk-accept"' in html
    body = " ".join(html.lower().split())
    for phrase in ("not financial advice", "at your own risk", "past performance", "as is"):
        assert phrase in body, f"login page is missing disclaimer text: {phrase!r}"
