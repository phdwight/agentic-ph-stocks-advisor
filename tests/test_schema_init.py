"""
Tests for eager schema initialisation at application startup.

The repository owns the schema (``initialize()`` runs ``CREATE TABLE IF
NOT EXISTS``), but it used to be reached only from inside request
handlers.  On a brand-new database that left a window where the tables
did not exist yet, and anything else reading them — the SQLAdmin panel,
a passkey registration — failed with ``UndefinedTable`` until some
unrelated request happened to arrive.

``create_app()`` now initialises the schema itself, and must do so
without becoming a new startup dependency on the database.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

import ph_stocks_advisor.web.app as _app_mod


@pytest.fixture
def _clean_settings(monkeypatch):
    """Build the app with auth disabled so create_app() is unencumbered."""
    from ph_stocks_advisor.infra.config import get_settings

    for var in (
        "ENTRA_CLIENT_ID",
        "ENTRA_CLIENT_SECRET",
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
    ):
        monkeypatch.delenv(var, raising=False)

    get_settings.cache_clear()
    s = get_settings()
    s.entra_client_id = ""
    s.entra_client_secret = ""
    s.google_client_id = ""
    s.google_client_secret = ""
    yield
    get_settings.cache_clear()


class TestSchemaInitAtStartup:
    def test_repository_is_built_during_create_app(self, _clean_settings):
        """The schema is created at boot, not deferred to the first request."""
        repo = MagicMock()
        with patch.object(_app_mod, "get_repository", return_value=repo) as get_repo:
            _app_mod.create_app()

        # get_repository() runs initialize() internally; being called at all
        # during create_app() is what closes the empty-database window.
        assert get_repo.called

    def test_startup_survives_an_unreachable_database(self, _clean_settings, caplog):
        """A database outage at boot must not stop the app from starting."""
        with (
            caplog.at_level(logging.WARNING, logger=_app_mod.logger.name),
            patch.object(
                _app_mod,
                "get_repository",
                side_effect=ConnectionError("could not connect to server"),
            ),
        ):
            app = _app_mod.create_app()

        assert app is not None
        assert any("schema init deferred" in r.message for r in caplog.records)

    def test_lazy_path_still_initialises_after_a_failed_startup(self, _clean_settings):
        """Once the database returns, the next request initialises the schema."""
        repo = MagicMock()
        repo.list_recent_symbols.return_value = []
        calls: list[str] = []

        def _flaky():
            calls.append("call")
            if len(calls) == 1:
                raise ConnectionError("could not connect to server")
            return repo

        with patch.object(_app_mod, "get_repository", side_effect=_flaky):
            app = _app_mod.create_app()  # first call fails, non-fatally
            app.config["TESTING"] = True
            with patch.object(_app_mod, "get_redis", return_value=MagicMock()):
                app.test_client().get("/healthz")

        assert len(calls) == 2
