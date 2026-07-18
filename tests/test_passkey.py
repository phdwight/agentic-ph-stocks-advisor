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


def test_register_begin_new_email_returns_options(pk_client):
    hdr = _csrf(pk_client)
    resp = pk_client.post(
        "/auth/passkey/register/begin", json={"email": "new@example.com", "name": "New User"}, headers=hdr
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert "challenge" in data
    assert data["user"]["id"]
    assert data["rp"]["id"] == "localhost"


def test_register_begin_existing_email_is_generic(pk_client):
    _seed_user_with_credential("taken@example.com")
    hdr = _csrf(pk_client)
    resp = pk_client.post(
        "/auth/passkey/register/begin", json={"email": "taken@example.com", "name": "Imposter"}, headers=hdr
    )
    assert resp.status_code == 400
    # The generic message must NOT reveal that the email is already registered.
    assert resp.get_json()["error"] == "Couldn't set up a passkey for that email."


def test_manage_endpoints_require_auth(pk_client):
    assert pk_client.get("/auth/passkey/list").status_code == 401
    hdr = _csrf(pk_client)
    assert pk_client.post("/auth/passkey/delete", json={"credential_id": "x"}, headers=hdr).status_code == 401
