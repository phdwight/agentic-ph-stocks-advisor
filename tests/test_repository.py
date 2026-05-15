"""
Tests for the repository layer (abstract interface, SQLite implementation,
and the repository factory).

All tests use an in-memory or temporary SQLite database — no external
services required.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest

from ph_stocks_advisor.data.models import FinalReport, Verdict
from ph_stocks_advisor.infra.config import Settings, _reset_repository, get_repository
from ph_stocks_advisor.infra.repository import AbstractReportRepository, ReportRecord
from ph_stocks_advisor.infra.repository_sqlite import SQLiteReportRepository

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_repo_singleton():
    """Reset the cached singleton between tests so each gets a fresh repo."""
    _reset_repository()
    yield
    _reset_repository()


@pytest.fixture
def sample_report() -> FinalReport:
    return FinalReport(
        symbol="TEL",
        verdict=Verdict.BUY,
        summary="TEL is a solid investment with good dividends.",
        price_section="Price is near midpoint.",
        dividend_section="Yield is attractive.",
        movement_section="Uptrend over the year.",
        valuation_section="Undervalued by 10%.",
        controversy_section="Minor spike in June.",
        sentiment_section="Global outlook is neutral.",
    )


@pytest.fixture
def sqlite_repo(tmp_path) -> Generator[SQLiteReportRepository]:
    """Create a fresh SQLite repo in a temp directory."""
    db_path = str(tmp_path / "test_reports.db")
    repo = SQLiteReportRepository(db_path=db_path)
    repo.initialize()
    yield repo
    repo.close()


# ---------------------------------------------------------------------------
# ReportRecord
# ---------------------------------------------------------------------------


class TestReportRecord:
    def test_from_final_report(self, sample_report: FinalReport):
        record = ReportRecord.from_final_report(sample_report)
        assert record.id is None
        assert record.symbol == "TEL"
        assert record.verdict == "BUY"
        assert "solid investment" in record.summary
        assert record.created_at is not None


# ---------------------------------------------------------------------------
# SQLite Repository
# ---------------------------------------------------------------------------


class TestSQLiteRepository:
    def test_implements_abstract(self, sqlite_repo):
        assert isinstance(sqlite_repo, AbstractReportRepository)

    def test_save_and_get_by_id(self, sqlite_repo, sample_report):
        record = ReportRecord.from_final_report(sample_report)
        record_id = sqlite_repo.save(record)
        assert record_id > 0
        assert record.id == record_id

        fetched = sqlite_repo.get_by_id(record_id)
        assert fetched is not None
        assert fetched.symbol == "TEL"
        assert fetched.verdict == "BUY"
        assert "solid investment" in fetched.summary

    def test_get_by_id_not_found(self, sqlite_repo):
        assert sqlite_repo.get_by_id(9999) is None

    def test_get_latest_by_symbol(self, sqlite_repo, sample_report):
        # Save two reports
        r1 = ReportRecord.from_final_report(sample_report)
        sqlite_repo.save(r1)

        # Modify and save a second
        report2 = sample_report.model_copy(update={"summary": "Updated analysis."})
        r2 = ReportRecord.from_final_report(report2)
        sqlite_repo.save(r2)

        latest = sqlite_repo.get_latest_by_symbol("TEL")
        assert latest is not None
        assert "Updated analysis" in latest.summary

    def test_get_latest_by_symbol_not_found(self, sqlite_repo):
        assert sqlite_repo.get_latest_by_symbol("NONEXIST") is None

    def test_list_by_symbol(self, sqlite_repo, sample_report):
        for i in range(5):
            r = ReportRecord.from_final_report(sample_report.model_copy(update={"summary": f"Report {i}"}))
            sqlite_repo.save(r)

        results = sqlite_repo.list_by_symbol("TEL", limit=3)
        assert len(results) == 3
        # Most recent first
        assert "Report 4" in results[0].summary

    def test_list_by_symbol_empty(self, sqlite_repo):
        results = sqlite_repo.list_by_symbol("NONE")
        assert results == []

    def test_close_and_reconnect(self, tmp_path, sample_report):
        db_path = str(tmp_path / "reconnect_test.db")
        repo = SQLiteReportRepository(db_path=db_path)
        repo.initialize()

        record = ReportRecord.from_final_report(sample_report)
        record_id = repo.save(record)
        repo.close()

        # Reopen and verify data persisted
        repo2 = SQLiteReportRepository(db_path=db_path)
        repo2.initialize()
        fetched = repo2.get_by_id(record_id)
        repo2.close()

        assert fetched is not None
        assert fetched.symbol == "TEL"

    def test_save_preserves_all_sections(self, sqlite_repo, sample_report):
        record = ReportRecord.from_final_report(sample_report)
        record_id = sqlite_repo.save(record)
        fetched = sqlite_repo.get_by_id(record_id)

        assert fetched.price_section == "Price is near midpoint."
        assert fetched.dividend_section == "Yield is attractive."
        assert fetched.movement_section == "Uptrend over the year."
        assert fetched.valuation_section == "Undervalued by 10%."
        assert fetched.controversy_section == "Minor spike in June."
        assert fetched.sentiment_section == "Global outlook is neutral."

    # ------------------------------------------------------------------
    # Per-user symbol tracking
    # ------------------------------------------------------------------

    def test_add_user_symbol_is_idempotent(self, sqlite_repo, sample_report):
        """Calling add_user_symbol twice for the same pair must not raise."""
        record = ReportRecord.from_final_report(sample_report)
        sqlite_repo.save(record)
        sqlite_repo.add_user_symbol("alice@test.com", "TEL")
        sqlite_repo.add_user_symbol("alice@test.com", "TEL")  # no error

    def test_list_user_symbols_returns_only_user_stocks(self, sqlite_repo, sample_report):
        """Each user should only see the symbols they have analysed."""
        # Save two different stock reports.
        r1 = ReportRecord.from_final_report(sample_report)
        sqlite_repo.save(r1)

        r2 = ReportRecord.from_final_report(sample_report.model_copy(update={"symbol": "SM"}))
        sqlite_repo.save(r2)

        # Alice analysed TEL only; Bob analysed SM only.
        sqlite_repo.add_user_symbol("alice@test.com", "TEL")
        sqlite_repo.add_user_symbol("bob@test.com", "SM")

        alice_stocks = sqlite_repo.list_user_symbols("alice@test.com")
        bob_stocks = sqlite_repo.list_user_symbols("bob@test.com")

        assert len(alice_stocks) == 1
        assert alice_stocks[0].symbol == "TEL"
        assert len(bob_stocks) == 1
        assert bob_stocks[0].symbol == "SM"

    def test_list_user_symbols_empty_for_new_user(self, sqlite_repo, sample_report):
        """A user who has never analysed anything sees an empty list."""
        record = ReportRecord.from_final_report(sample_report)
        sqlite_repo.save(record)
        assert sqlite_repo.list_user_symbols("nobody@test.com") == []

    def test_list_user_symbols_returns_latest_report(self, sqlite_repo, sample_report):
        """When multiple reports exist for a symbol, the latest is returned."""
        r1 = ReportRecord.from_final_report(sample_report)
        sqlite_repo.save(r1)

        r2 = ReportRecord.from_final_report(sample_report.model_copy(update={"summary": "Updated TEL analysis."}))
        sqlite_repo.save(r2)

        sqlite_repo.add_user_symbol("alice@test.com", "TEL")
        results = sqlite_repo.list_user_symbols("alice@test.com")
        assert len(results) == 1
        assert "Updated TEL analysis" in results[0].summary


# ---------------------------------------------------------------------------
# Repository factory
# ---------------------------------------------------------------------------


class TestGetRepository:
    def test_sqlite_backend(self, tmp_path):
        settings = Settings()
        settings.db_backend = "sqlite"
        settings.sqlite_path = str(tmp_path / "factory_test.db")
        repo = get_repository(settings)
        assert isinstance(repo, SQLiteReportRepository)
        repo.close()

    def test_postgres_import(self):
        """Verify the Postgres repo class can at least be imported."""
        pytest.importorskip("psycopg2", reason="psycopg2 not installed")
        from ph_stocks_advisor.infra.repository_postgres import PostgresReportRepository

        assert issubclass(PostgresReportRepository, AbstractReportRepository)


# ---------------------------------------------------------------------------
# Postgres connection-recycling behaviour (regression test for stale pooled
# connections raising ``OperationalError('server closed the connection
# unexpectedly')`` in long-running Celery workers).
# ---------------------------------------------------------------------------


class TestPostgresConnectionRecycling:
    """Verify that dead pooled connections are detected and replaced."""

    def _make_repo(self):
        pytest.importorskip("psycopg2", reason="psycopg2 not installed")
        from ph_stocks_advisor.infra.repository_postgres import PostgresReportRepository

        # Avoid touching a real DB — we only exercise the pool wrapper.
        return PostgresReportRepository("postgresql://unused", min_conn=1, max_conn=2)

    def _fake_conn(self, *, alive: bool):
        """Build a stand-in for a psycopg2 connection.

        ``alive=True`` connections respond to ``SELECT 1`` normally;
        dead ones raise ``OperationalError`` from ``cursor.execute`` to
        mimic a server-side disconnect.
        """
        import psycopg2  # type: ignore[import-untyped]

        class _Cursor:
            def __init__(self, alive: bool) -> None:
                self._alive = alive

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def execute(self, *_args, **_kwargs):
                if not self._alive:
                    raise psycopg2.OperationalError(
                        "server closed the connection unexpectedly",
                    )

            def fetchone(self):
                return (1,)

        class _Conn:
            def __init__(self, alive: bool) -> None:
                self._alive = alive
                self.closed = 0
                self.rolled_back = False

            def cursor(self, **_kwargs):
                return _Cursor(self._alive)

            def rollback(self):
                self.rolled_back = True

        return _Conn(alive)

    def test_dead_connection_is_discarded_and_replaced(self, monkeypatch):
        """A stale pooled connection must be closed and a fresh one returned."""
        repo = self._make_repo()

        dead = self._fake_conn(alive=False)
        live = self._fake_conn(alive=True)
        checked_out = [dead, live]
        returned: list[tuple[object, bool]] = []

        class _FakePool:
            closed = False

            def getconn(self):
                return checked_out.pop(0)

            def putconn(self, conn, close=False):
                returned.append((conn, close))

        monkeypatch.setattr(repo, "_get_pool", lambda: _FakePool())

        with repo._conn() as conn:
            assert conn is live, "Caller should receive the healthy connection."

        # Dead connection must be returned with close=True so the pool
        # opens a fresh socket next time; the live one returns normally.
        assert (dead, True) in returned
        assert (live, False) in returned

    def test_operational_error_during_use_marks_conn_broken(self, monkeypatch):
        """If the connection dies mid-request, it must not return to the pool alive."""
        import psycopg2  # type: ignore[import-untyped]

        repo = self._make_repo()
        live = self._fake_conn(alive=True)
        returned: list[tuple[object, bool]] = []

        class _FakePool:
            closed = False

            def getconn(self):
                return live

            def putconn(self, conn, close=False):
                returned.append((conn, close))

        monkeypatch.setattr(repo, "_get_pool", lambda: _FakePool())

        with pytest.raises(psycopg2.OperationalError):
            with repo._conn():
                raise psycopg2.OperationalError("server closed the connection unexpectedly")

        assert returned == [(live, True)], "Broken connection must be discarded."
