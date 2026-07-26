"""Version-sync contract (CI auto-bump, tag-driven).

Three things must never drift: the version the running app reports, the git
tag, and the published image tag. CI enforces tag↔image; these tests pin the
app side — the runtime version must come from the shipped pyproject.toml and
be reachable both in the UI footer and as a machine-readable endpoint.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ph_stocks_advisor.web.app import _app_version, create_app

_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+([-+.][0-9A-Za-z.-]+)?$")


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def test_pyproject_version_is_semver():
    """CI's bump job parses this line — it must stay X.Y.Z."""
    text = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert m, "pyproject.toml has no top-level version line"
    assert re.fullmatch(r"\d+\.\d+\.\d+", m.group(1)), (
        f"version {m.group(1)!r} must be X.Y.Z — CI's bump job compares it as a floor"
    )


def test_app_version_matches_pyproject():
    """The running app reports the shipped version file, not a stale constant."""
    text = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
    expected = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE).group(1)  # type: ignore[union-attr]
    assert _app_version() == expected


def test_version_endpoint_reports_the_running_version(client):
    """CI pulls the published image and asserts this value equals the tag."""
    resp = client.get("/version")
    assert resp.status_code == 200
    body = resp.get_json()
    assert _VERSION_RE.fullmatch(body["version"]), body
    assert body["version"] == _app_version()


def test_version_endpoint_is_public(client):
    """Must not sit behind auth — deploy smoke-checks call it unauthenticated."""
    assert client.get("/version").status_code == 200


def test_footer_renders_the_same_version(client):
    """The UI and the endpoint must never disagree."""
    html = client.get("/").get_data(as_text=True)
    assert f"v{_app_version()}" in html
