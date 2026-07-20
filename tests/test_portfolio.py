"""
Tests for the portfolio holdings feature.

Covers:
- HoldingRecord and PortfolioReportRecord data classes
- Holdings CRUD in the SQLite repository
- Portfolio report persistence
- PortfolioAgent execution with a mock LLM
- Holdings API endpoints (elevated-only access)
- Portfolio analysis endpoint
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from ph_stocks_advisor.data.models import FinalReport, Verdict
from ph_stocks_advisor.infra.repository import (
    HoldingRecord,
    PortfolioReportRecord,
    ReportRecord,
)
from ph_stocks_advisor.infra.repository_sqlite import SQLiteReportRepository

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sqlite_repo(tmp_path) -> Generator[SQLiteReportRepository]:
    """Fresh SQLite repo with all tables created."""
    db_path = str(tmp_path / "test_portfolio.db")
    repo = SQLiteReportRepository(db_path=db_path)
    repo.initialize()
    yield repo
    repo.close()


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
    )


# ---------------------------------------------------------------------------
# HoldingRecord unit tests
# ---------------------------------------------------------------------------


class TestHoldingRecord:
    def test_total_cost(self):
        h = HoldingRecord(
            user_id="alice@test.com",
            symbol="TEL",
            shares=1000,
            avg_cost=25.50,
        )
        assert h.total_cost == 25_500.0

    def test_repr(self):
        h = HoldingRecord(user_id="alice@test.com", symbol="TEL", shares=100, avg_cost=10.0)
        assert "TEL" in repr(h)
        assert "alice@test.com" in repr(h)


# ---------------------------------------------------------------------------
# PortfolioReportRecord unit tests
# ---------------------------------------------------------------------------


class TestPortfolioReportRecord:
    def test_repr(self):
        pr = PortfolioReportRecord(
            id=1,
            user_id="alice@test.com",
            symbol="TEL",
            shares=500,
            avg_cost=30.0,
            analysis="Hold for now.",
        )
        assert "TEL" in repr(pr)
        assert "alice@test.com" in repr(pr)


# ---------------------------------------------------------------------------
# SQLite Repository — Holdings CRUD
# ---------------------------------------------------------------------------


class TestSQLiteHoldings:
    def test_save_and_get_holding(self, sqlite_repo):
        h = HoldingRecord(user_id="alice@test.com", symbol="TEL", shares=1000, avg_cost=25.0)
        sqlite_repo.save_holding(h)

        fetched = sqlite_repo.get_holding("alice@test.com", "TEL")
        assert fetched is not None
        assert fetched.shares == 1000
        assert fetched.avg_cost == 25.0
        assert fetched.symbol == "TEL"

    def test_save_holding_upsert(self, sqlite_repo):
        """Saving the same user+symbol again should update, not duplicate."""
        h1 = HoldingRecord(user_id="alice@test.com", symbol="TEL", shares=1000, avg_cost=25.0)
        sqlite_repo.save_holding(h1)

        h2 = HoldingRecord(user_id="alice@test.com", symbol="TEL", shares=2000, avg_cost=22.0)
        sqlite_repo.save_holding(h2)

        fetched = sqlite_repo.get_holding("alice@test.com", "TEL")
        assert fetched is not None
        assert fetched.shares == 2000
        assert fetched.avg_cost == 22.0

    def test_get_holding_not_found(self, sqlite_repo):
        assert sqlite_repo.get_holding("nobody@test.com", "TEL") is None

    def test_delete_holding(self, sqlite_repo):
        h = HoldingRecord(user_id="alice@test.com", symbol="SM", shares=500, avg_cost=100.0)
        sqlite_repo.save_holding(h)
        sqlite_repo.delete_holding("alice@test.com", "SM")
        assert sqlite_repo.get_holding("alice@test.com", "SM") is None

    def test_delete_holding_nonexistent_is_noop(self, sqlite_repo):
        """Deleting a holding that does not exist should not raise."""
        sqlite_repo.delete_holding("nobody@test.com", "XYZ")

    def test_list_holdings(self, sqlite_repo):
        sqlite_repo.save_holding(HoldingRecord(user_id="alice@test.com", symbol="TEL", shares=100, avg_cost=25.0))
        sqlite_repo.save_holding(HoldingRecord(user_id="alice@test.com", symbol="SM", shares=200, avg_cost=1000.0))
        sqlite_repo.save_holding(HoldingRecord(user_id="bob@test.com", symbol="BDO", shares=50, avg_cost=150.0))

        alice_holdings = sqlite_repo.list_holdings("alice@test.com")
        assert len(alice_holdings) == 2
        symbols = {h.symbol for h in alice_holdings}
        assert symbols == {"SM", "TEL"}

        bob_holdings = sqlite_repo.list_holdings("bob@test.com")
        assert len(bob_holdings) == 1
        assert bob_holdings[0].symbol == "BDO"

    def test_list_holdings_empty(self, sqlite_repo):
        assert sqlite_repo.list_holdings("nobody@test.com") == []

    def test_holding_symbol_uppercased(self, sqlite_repo):
        """Saving with lowercase symbol should store as uppercase."""
        h = HoldingRecord(user_id="alice@test.com", symbol="tel", shares=100, avg_cost=25.0)
        sqlite_repo.save_holding(h)
        assert sqlite_repo.get_holding("alice@test.com", "TEL") is not None


# ---------------------------------------------------------------------------
# SQLite Repository — Portfolio Reports
# ---------------------------------------------------------------------------


class TestSQLitePortfolioReports:
    def test_save_and_get_portfolio_report(self, sqlite_repo, sample_report):
        record = ReportRecord.from_final_report(sample_report)
        report_id = sqlite_repo.save(record)

        pr = PortfolioReportRecord(
            id=None,
            user_id="alice@test.com",
            symbol="TEL",
            shares=1000,
            avg_cost=25.0,
            analysis="Hold position — TEL is undervalued.",
            base_report_id=report_id,
        )
        pr_id = sqlite_repo.save_portfolio_report(pr)
        assert pr_id > 0
        assert pr.id == pr_id

        fetched = sqlite_repo.get_portfolio_report("alice@test.com", "TEL")
        assert fetched is not None
        assert fetched.analysis == "Hold position — TEL is undervalued."
        assert fetched.shares == 1000
        assert fetched.avg_cost == 25.0
        assert fetched.base_report_id == report_id

    def test_get_portfolio_report_returns_latest(self, sqlite_repo):
        pr1 = PortfolioReportRecord(
            id=None,
            user_id="alice@test.com",
            symbol="TEL",
            shares=500,
            avg_cost=30.0,
            analysis="First analysis.",
        )
        sqlite_repo.save_portfolio_report(pr1)

        pr2 = PortfolioReportRecord(
            id=None,
            user_id="alice@test.com",
            symbol="TEL",
            shares=1000,
            avg_cost=28.0,
            analysis="Updated analysis.",
        )
        sqlite_repo.save_portfolio_report(pr2)

        fetched = sqlite_repo.get_portfolio_report("alice@test.com", "TEL")
        assert fetched is not None
        assert "Updated analysis" in fetched.analysis

    def test_get_portfolio_report_user_scoped(self, sqlite_repo):
        """Alice's portfolio report should not be visible to Bob."""
        pr = PortfolioReportRecord(
            id=None,
            user_id="alice@test.com",
            symbol="TEL",
            shares=500,
            avg_cost=30.0,
            analysis="Alice's analysis.",
        )
        sqlite_repo.save_portfolio_report(pr)

        assert sqlite_repo.get_portfolio_report("bob@test.com", "TEL") is None

    def test_get_portfolio_report_not_found(self, sqlite_repo):
        assert sqlite_repo.get_portfolio_report("nobody@test.com", "XYZ") is None


# ---------------------------------------------------------------------------
# PortfolioAgent
# ---------------------------------------------------------------------------


class TestPortfolioAgent:
    def test_portfolio_agent_generates_analysis(self):
        from ph_stocks_advisor.agents.portfolio import PortfolioAgent
        from tests.conftest import make_mock_llm

        llm = make_mock_llm("**Recommendation: HOLD** — TEL is undervalued with strong dividend yield.")
        agent = PortfolioAgent(llm)
        result = agent.run(
            symbol="TEL",
            shares=1000,
            avg_cost=25.0,
            current_price=30.0,
            base_report="TEL is a solid investment with good dividends.",
            sentiment_context="Global outlook is neutral with no major geopolitical risks.",
        )
        assert "HOLD" in result
        llm.invoke.assert_called_once()

    def test_portfolio_agent_handles_zero_cost(self):
        """When total cost is zero, unrealised P/L % should not crash."""
        from ph_stocks_advisor.agents.portfolio import PortfolioAgent
        from tests.conftest import make_mock_llm

        llm = make_mock_llm("Recommendation: ACCUMULATE")
        agent = PortfolioAgent(llm)
        # avg_cost=0 means total_cost=0 — the agent should handle this.
        result = agent.run(
            symbol="TEL",
            shares=0,
            avg_cost=0,
            current_price=30.0,
            base_report="Report text.",
            sentiment_context="",
        )
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# API Endpoint tests (Flask test client)
# ---------------------------------------------------------------------------


@pytest.fixture
def app(tmp_path):
    """Create a Flask test app with a temporary SQLite backend."""
    from ph_stocks_advisor.infra.config import Settings, _reset_repository
    from ph_stocks_advisor.web.app import create_app

    _reset_repository()

    settings = Settings()
    settings.db_backend = "sqlite"
    settings.sqlite_path = str(tmp_path / "test_api.db")

    with (
        patch("ph_stocks_advisor.web.app.get_settings", return_value=settings),
        patch("ph_stocks_advisor.web.app.get_redis") as mock_redis,
        patch("ph_stocks_advisor.infra.config.get_settings", return_value=settings),
    ):
        mock_redis_instance = MagicMock()
        mock_redis.return_value = mock_redis_instance
        mock_redis_instance.ping.return_value = True

        application = create_app()
        application.config["TESTING"] = True

        # Initialize the repo.
        from ph_stocks_advisor.infra.config import get_repository

        repo = get_repository(settings)
        repo.initialize()

        yield application

    _reset_repository()


@pytest.fixture
def client(app):
    return app.test_client()


def _set_elevated_user(client):
    """Helper to set session as an elevated user."""
    with client.session_transaction() as sess:
        sess["user"] = {
            "name": "Test Elevated",
            "email": "elevated@test.com",
            "oid": "test-elevated-oid",
            "provider": "local",
            "user_type": 1,
        }


def _set_normal_user(client):
    """Helper to set session as a normal user."""
    with client.session_transaction() as sess:
        sess["user"] = {
            "name": "Test Normal",
            "email": "normal@test.com",
            "oid": "test-normal-oid",
            "provider": "local",
            "user_type": 0,
        }


class TestHoldingsAPI:
    def test_get_holding_requires_elevated(self, client):
        _set_normal_user(client)
        resp = client.get("/api/holdings/TEL")
        assert resp.status_code == 403

    def test_get_holding_empty(self, client):
        _set_elevated_user(client)
        resp = client.get("/api/holdings/TEL")
        assert resp.status_code == 200
        assert resp.get_json()["holding"] is None

    def test_save_and_get_holding(self, client):
        _set_elevated_user(client)

        # Save.
        resp = client.post(
            "/api/holdings/TEL",
            json={"shares": 1000, "avg_cost": 25.50},
        )
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "saved"

        # Retrieve.
        resp = client.get("/api/holdings/TEL")
        assert resp.status_code == 200
        data = resp.get_json()["holding"]
        assert data["shares"] == 1000
        assert data["avg_cost"] == 25.50

    def test_save_holding_validation(self, client):
        _set_elevated_user(client)

        resp = client.post(
            "/api/holdings/TEL",
            json={"shares": -1, "avg_cost": 25.0},
        )
        assert resp.status_code == 400

        resp = client.post(
            "/api/holdings/TEL",
            json={"shares": 100, "avg_cost": 0},
        )
        assert resp.status_code == 400

    def test_delete_holding(self, client):
        _set_elevated_user(client)

        client.post("/api/holdings/TEL", json={"shares": 500, "avg_cost": 30.0})
        resp = client.delete("/api/holdings/TEL")
        assert resp.status_code == 200

        resp = client.get("/api/holdings/TEL")
        assert resp.get_json()["holding"] is None

    def test_delete_holding_requires_elevated(self, client):
        _set_normal_user(client)
        resp = client.delete("/api/holdings/TEL")
        assert resp.status_code == 403

    def test_portfolio_report_requires_elevated(self, client):
        _set_normal_user(client)
        resp = client.get("/api/portfolio-report/TEL")
        assert resp.status_code == 403

    def test_portfolio_report_empty(self, client):
        _set_elevated_user(client)
        resp = client.get("/api/portfolio-report/TEL")
        assert resp.status_code == 200
        assert resp.get_json()["report"] is None

    def test_portfolio_analyse_requires_holding(self, client):
        _set_elevated_user(client)
        with patch("ph_stocks_advisor.web.app._is_past_cutoff", return_value=True):
            resp = client.post("/api/portfolio-analyse/TEL")
        assert resp.status_code == 400
        assert "No holding found" in resp.get_json()["error"]


# ---------------------------------------------------------------------------
# Portfolio analysis daily cooldown
# ---------------------------------------------------------------------------


class TestPortfolioCooldown:
    """Portfolio analysis can only run once per stock per day (resets at 3:00 PM PHT)."""

    def test_portfolio_analyse_blocked_when_already_run_today(self, client):
        """Second portfolio analysis on the same day returns 429."""
        _set_elevated_user(client)

        # Save a holding.
        client.post("/api/holdings/TEL", json={"shares": 1000, "avg_cost": 25.0})

        # Seed a base report and a portfolio report created "now" (today).
        from ph_stocks_advisor.data.models import FinalReport, Verdict
        from ph_stocks_advisor.infra.config import get_repository
        from ph_stocks_advisor.infra.repository import (
            PortfolioReportRecord,
            ReportRecord,
        )

        repo = get_repository()
        report = FinalReport(
            symbol="TEL",
            verdict=Verdict.BUY,
            summary="Good stock.",
            price_section="Price ok.",
            dividend_section="Dividends ok.",
            movement_section="Movement ok.",
            valuation_section="Valuation ok.",
            controversy_section="No issues.",
        )
        base_id = repo.save(ReportRecord.from_final_report(report))

        # Save a portfolio report created "now".
        pr = PortfolioReportRecord(
            id=None,
            user_id="elevated@test.com",
            symbol="TEL",
            shares=1000,
            avg_cost=25.0,
            analysis="Hold for now.",
            base_report_id=base_id,
        )
        repo.save_portfolio_report(pr)

        # Try to analyse again — should be blocked.
        with patch("ph_stocks_advisor.web.app._is_past_cutoff", return_value=True):
            resp = client.post("/api/portfolio-analyse/TEL")
        assert resp.status_code == 429
        data = resp.get_json()
        assert "already run today" in data["error"]
        assert "reset_at" in data

    def test_portfolio_analyse_allowed_after_cooldown(self, client):
        """Portfolio analysis is allowed when the existing report is from yesterday."""
        _set_elevated_user(client)

        client.post("/api/holdings/TEL", json={"shares": 1000, "avg_cost": 25.0})

        from datetime import timedelta

        from ph_stocks_advisor.data.models import FinalReport, Verdict
        from ph_stocks_advisor.infra.config import get_repository
        from ph_stocks_advisor.infra.repository import (
            PortfolioReportRecord,
            ReportRecord,
        )

        repo = get_repository()
        report = FinalReport(
            symbol="TEL",
            verdict=Verdict.BUY,
            summary="Good stock.",
            price_section="Price ok.",
            dividend_section="Dividends ok.",
            movement_section="Movement ok.",
            valuation_section="Valuation ok.",
            controversy_section="No issues.",
        )
        base_id = repo.save(ReportRecord.from_final_report(report))

        # Save a portfolio report with a timestamp from yesterday.
        pr = PortfolioReportRecord(
            id=None,
            user_id="elevated@test.com",
            symbol="TEL",
            shares=1000,
            avg_cost=25.0,
            analysis="Old analysis.",
            base_report_id=base_id,
        )
        repo.save_portfolio_report(pr)

        # Backdate created_at clearly before the last trading close so the
        # cooldown has deterministically passed (trading-day-aware cutoff).
        import sqlite3

        conn = sqlite3.connect(repo._db_path)  # type: ignore[attr-defined]
        stale = (datetime.now(UTC) - timedelta(days=10)).isoformat()
        conn.execute(
            "UPDATE portfolio_reports SET created_at = ? WHERE user_id = ? AND symbol = ?",
            (stale, "elevated@test.com", "TEL"),
        )
        conn.commit()
        conn.close()

        # Now the cooldown should have passed — the endpoint should accept (needs Celery mock).
        with (
            patch("ph_stocks_advisor.web.app._is_past_cutoff", return_value=True),
            patch("ph_stocks_advisor.web.tasks.portfolio_analyse_stock") as mock_task,
        ):
            mock_result = MagicMock()
            mock_result.id = "task-123"
            mock_task.delay.return_value = mock_result

            resp = client.post("/api/portfolio-analyse/TEL")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["status"] == "started"

    def test_portfolio_cooldown_per_stock(self, client):
        """Cooldown is per-stock: running for TEL doesn't block SM."""
        _set_elevated_user(client)

        # Save holdings for both symbols.
        client.post("/api/holdings/TEL", json={"shares": 1000, "avg_cost": 25.0})
        client.post("/api/holdings/SM", json={"shares": 500, "avg_cost": 1000.0})

        from ph_stocks_advisor.data.models import FinalReport, Verdict
        from ph_stocks_advisor.infra.config import get_repository
        from ph_stocks_advisor.infra.repository import (
            PortfolioReportRecord,
            ReportRecord,
        )

        repo = get_repository()

        # Seed base reports for both symbols.
        for sym in ("TEL", "SM"):
            r = FinalReport(
                symbol=sym,
                verdict=Verdict.BUY,
                summary=f"{sym} is good.",
                price_section="P",
                dividend_section="D",
                movement_section="M",
                valuation_section="V",
                controversy_section="C",
            )
            repo.save(ReportRecord.from_final_report(r))

        # Portfolio report for TEL today.
        pr = PortfolioReportRecord(
            id=None,
            user_id="elevated@test.com",
            symbol="TEL",
            shares=1000,
            avg_cost=25.0,
            analysis="TEL analysis.",
        )
        repo.save_portfolio_report(pr)

        # TEL should be blocked.
        with patch("ph_stocks_advisor.web.app._is_past_cutoff", return_value=True):
            resp = client.post("/api/portfolio-analyse/TEL")
        assert resp.status_code == 429

        # SM should be allowed.
        with (
            patch("ph_stocks_advisor.web.app._is_past_cutoff", return_value=True),
            patch("ph_stocks_advisor.web.tasks.portfolio_analyse_stock") as mock_task,
        ):
            mock_result = MagicMock()
            mock_result.id = "task-sm"
            mock_task.delay.return_value = mock_result

            resp = client.post("/api/portfolio-analyse/SM")
            assert resp.status_code == 200
            assert resp.get_json()["status"] == "started"


# ---------------------------------------------------------------------------
# Portfolio analysis 3 PM PHT gate
# ---------------------------------------------------------------------------


class TestPortfolioTimingGate:
    """Portfolio analysis is only available after 3:00 PM PHT."""

    def test_portfolio_analyse_blocked_before_3pm_pht(self, client):
        """Before 3 PM PHT the endpoint returns 425 Too Early."""
        _set_elevated_user(client)
        client.post("/api/holdings/TEL", json={"shares": 1000, "avg_cost": 25.0})

        with patch("ph_stocks_advisor.web.app._is_past_cutoff", return_value=False):
            resp = client.post("/api/portfolio-analyse/TEL")

        assert resp.status_code == 425
        data = resp.get_json()
        assert "3:00" in data["error"]
        assert "available_at" in data

    def test_portfolio_analyse_allowed_after_3pm_pht(self, client):
        """After 3 PM PHT the endpoint proceeds normally."""
        _set_elevated_user(client)
        client.post("/api/holdings/TEL", json={"shares": 1000, "avg_cost": 25.0})

        # Seed a fresh base report.
        from ph_stocks_advisor.data.models import FinalReport, Verdict
        from ph_stocks_advisor.infra.config import get_repository
        from ph_stocks_advisor.infra.repository import ReportRecord

        repo = get_repository()
        report = FinalReport(
            symbol="TEL",
            verdict=Verdict.BUY,
            summary="Good stock.",
            price_section="P",
            dividend_section="D",
            movement_section="M",
            valuation_section="V",
            controversy_section="C",
        )
        repo.save(ReportRecord.from_final_report(report))

        with (
            patch("ph_stocks_advisor.web.app._is_past_cutoff", return_value=True),
            patch("ph_stocks_advisor.web.tasks.portfolio_analyse_stock") as mock_task,
        ):
            mock_result = MagicMock()
            mock_result.id = "task-123"
            mock_task.delay.return_value = mock_result

            resp = client.post("/api/portfolio-analyse/TEL")

        assert resp.status_code == 200
        assert resp.get_json()["status"] == "started"


# ---------------------------------------------------------------------------
# Auto-chain: base analysis dispatched when no fresh report exists
# ---------------------------------------------------------------------------


class TestPortfolioAutoChain:
    """When no fresh base report exists, portfolio-analyse auto-triggers base analysis first."""

    def test_chains_base_analysis_when_no_report(self, client):
        """If no report exists at all, a linked base+portfolio analysis is dispatched."""
        _set_elevated_user(client)
        client.post("/api/holdings/TEL", json={"shares": 1000, "avg_cost": 25.0})

        mock_base_result = MagicMock()
        mock_base_result.id = "base-task-001"

        mock_base_sig = MagicMock()
        mock_base_sig.apply_async.return_value = mock_base_result

        with (
            patch("ph_stocks_advisor.web.app._is_past_cutoff", return_value=True),
            patch("ph_stocks_advisor.web.app.get_redis") as mock_redis,
            patch("ph_stocks_advisor.web.tasks.analyse_stock") as mock_analyse,
        ):
            mock_redis_inst = MagicMock()
            mock_redis.return_value = mock_redis_inst
            mock_analyse.s.return_value = mock_base_sig

            resp = client.post("/api/portfolio-analyse/TEL")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "started"
        assert data["chained"] is True
        assert data["task_id"] == "base-task-001"
        # A pre-assigned portfolio task ID must be included so the
        # frontend can poll it after the base task finishes.
        assert "portfolio_task_id" in data
        assert data["portfolio_task_id"]  # non-empty string
        # Verify apply_async was called with link kwarg
        mock_base_sig.apply_async.assert_called_once()
        _, kwargs = mock_base_sig.apply_async.call_args
        assert "link" in kwargs

    def test_chains_base_analysis_when_report_stale(self, client):
        """If the latest report is older than the cutoff, chains base+portfolio."""
        _set_elevated_user(client)
        client.post("/api/holdings/TEL", json={"shares": 1000, "avg_cost": 25.0})

        # Seed a stale base report (backdate past the cutoff).
        from datetime import timedelta

        from ph_stocks_advisor.data.models import FinalReport, Verdict
        from ph_stocks_advisor.infra.config import get_repository
        from ph_stocks_advisor.infra.repository import ReportRecord

        repo = get_repository()
        report = FinalReport(
            symbol="TEL",
            verdict=Verdict.BUY,
            summary="Old report.",
            price_section="P",
            dividend_section="D",
            movement_section="M",
            valuation_section="V",
            controversy_section="C",
        )
        repo.save(ReportRecord.from_final_report(report))

        import sqlite3

        conn = sqlite3.connect(repo._db_path)  # type: ignore[attr-defined]
        old_ts = (datetime.now(UTC) - timedelta(days=2)).isoformat()
        conn.execute("UPDATE reports SET created_at = ? WHERE symbol = ?", (old_ts, "TEL"))
        conn.commit()
        conn.close()

        mock_base_result = MagicMock()
        mock_base_result.id = "base-task-002"

        mock_base_sig = MagicMock()
        mock_base_sig.apply_async.return_value = mock_base_result

        with (
            patch("ph_stocks_advisor.web.app._is_past_cutoff", return_value=True),
            patch("ph_stocks_advisor.web.app.get_redis") as mock_redis,
            patch("ph_stocks_advisor.web.tasks.analyse_stock") as mock_analyse,
        ):
            mock_redis_inst = MagicMock()
            mock_redis.return_value = mock_redis_inst
            mock_analyse.s.return_value = mock_base_sig

            resp = client.post("/api/portfolio-analyse/TEL")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["chained"] is True

    def test_inflight_lock_uses_base_task_id(self, client):
        """The inflight dedup lock should store the base task's ID, not the chain's.

        Regression: previously the chain result ID (portfolio task) was stored,
        so a second /analyse request would join a task whose SSE events never
        arrive, causing the UI to hang at "Queued...".
        """
        _set_elevated_user(client)
        client.post("/api/holdings/MBT", json={"shares": 500, "avg_cost": 50.0})

        mock_base_result = MagicMock()
        mock_base_result.id = "base-task-mbt"

        mock_base_sig = MagicMock()
        mock_base_sig.apply_async.return_value = mock_base_result

        with (
            patch("ph_stocks_advisor.web.app._is_past_cutoff", return_value=True),
            patch("ph_stocks_advisor.web.app.get_redis") as mock_redis,
            patch("ph_stocks_advisor.web.tasks.analyse_stock") as mock_analyse,
        ):
            mock_redis_inst = MagicMock()
            mock_redis.return_value = mock_redis_inst
            mock_analyse.s.return_value = mock_base_sig

            resp = client.post("/api/portfolio-analyse/MBT")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["task_id"] == "base-task-mbt"

        # The inflight lock must have been set with the base task's ID.
        calls = mock_redis_inst.set.call_args_list
        inflight_call = [c for c in calls if c[0][0] == "analysis:inflight:MBT"]
        assert len(inflight_call) == 1
        assert inflight_call[0][0][1] == "base-task-mbt"

        # The reverse mapping must also use the base task ID.
        reverse_call = [c for c in calls if c[0][0] == "analysis:task:base-task-mbt"]
        assert len(reverse_call) == 1

    def test_portfolio_task_id_set_on_linked_signature(self, client):
        """The portfolio callback signature must have a pre-assigned task ID
        so the frontend can poll it independently after the base task
        completes, preventing display of a stale portfolio report."""
        _set_elevated_user(client)
        client.post("/api/holdings/GLO", json={"shares": 200, "avg_cost": 30.0})

        mock_base_result = MagicMock()
        mock_base_result.id = "base-task-glo"

        mock_base_sig = MagicMock()
        mock_base_sig.apply_async.return_value = mock_base_result

        with (
            patch("ph_stocks_advisor.web.app._is_past_cutoff", return_value=True),
            patch("ph_stocks_advisor.web.app.get_redis") as mock_redis,
            patch("ph_stocks_advisor.web.tasks.analyse_stock") as mock_analyse,
            patch("ph_stocks_advisor.web.tasks.portfolio_analyse_stock") as mock_portfolio,
        ):
            mock_redis_inst = MagicMock()
            mock_redis.return_value = mock_redis_inst
            mock_analyse.s.return_value = mock_base_sig

            mock_portfolio_sig = MagicMock()
            mock_portfolio.s.return_value = mock_portfolio_sig
            mock_portfolio_sig.set.return_value = mock_portfolio_sig

            resp = client.post("/api/portfolio-analyse/GLO")

        assert resp.status_code == 200
        data = resp.get_json()

        # The response must include a portfolio_task_id.
        ptid = data["portfolio_task_id"]
        assert ptid

        # The portfolio signature must have been configured with .set(task_id=...).
        mock_portfolio_sig.set.assert_called_once_with(task_id=ptid)

        # The linked task passed to apply_async must be the set()-configured sig.
        _, apply_kwargs = mock_base_sig.apply_async.call_args
        linked_tasks = apply_kwargs["link"]
        assert linked_tasks == [mock_portfolio_sig]


class TestPortfolioInflight:
    """Portfolio inflight lock prevents stale report display on page refresh."""

    def test_chained_dispatch_sets_portfolio_inflight_lock(self, client):
        """When a chained base+portfolio analysis is dispatched, the portfolio
        inflight lock is set in Redis so a page refresh shows a spinner."""
        _set_elevated_user(client)
        client.post("/api/holdings/TEL", json={"shares": 500, "avg_cost": 20.0})

        mock_base_result = MagicMock()
        mock_base_result.id = "base-task-chained"
        mock_base_sig = MagicMock()
        mock_base_sig.apply_async.return_value = mock_base_result

        with (
            patch("ph_stocks_advisor.web.app._is_past_cutoff", return_value=True),
            patch("ph_stocks_advisor.web.app.get_redis") as mock_redis,
            patch("ph_stocks_advisor.web.tasks.analyse_stock") as mock_analyse,
        ):
            mock_redis_inst = MagicMock()
            mock_redis.return_value = mock_redis_inst
            mock_analyse.s.return_value = mock_base_sig

            resp = client.post("/api/portfolio-analyse/TEL")

        data = resp.get_json()
        ptid = data["portfolio_task_id"]

        # Verify portfolio inflight lock was set.
        set_calls = mock_redis_inst.set.call_args_list
        pf_inflight_call = [c for c in set_calls if c[0][0] == "portfolio:inflight:elevated@test.com:TEL"]
        assert len(pf_inflight_call) == 1
        assert pf_inflight_call[0][0][1] == ptid

    def test_standalone_dispatch_sets_portfolio_inflight_lock(self, client):
        """When a standalone portfolio analysis is dispatched (fresh base exists),
        the portfolio inflight lock is set in Redis."""
        _set_elevated_user(client)
        client.post("/api/holdings/TEL", json={"shares": 500, "avg_cost": 20.0})

        # Seed a fresh base report.
        from ph_stocks_advisor.infra.config import get_repository

        repo = get_repository()
        report = FinalReport(
            symbol="TEL",
            verdict=Verdict.BUY,
            summary="Fresh report.",
            price_section="P",
            dividend_section="D",
            movement_section="M",
            valuation_section="V",
            controversy_section="C",
        )
        repo.save(ReportRecord.from_final_report(report))

        with (
            patch("ph_stocks_advisor.web.app._is_past_cutoff", return_value=True),
            patch("ph_stocks_advisor.web.app.get_redis") as mock_redis,
            patch("ph_stocks_advisor.web.tasks.portfolio_analyse_stock") as mock_task,
        ):
            mock_redis_inst = MagicMock()
            mock_redis.return_value = mock_redis_inst
            mock_result = MagicMock()
            mock_result.id = "pf-task-standalone"
            mock_task.delay.return_value = mock_result

            resp = client.post("/api/portfolio-analyse/TEL")

        assert resp.status_code == 200

        set_calls = mock_redis_inst.set.call_args_list
        pf_inflight_call = [c for c in set_calls if c[0][0] == "portfolio:inflight:elevated@test.com:TEL"]
        assert len(pf_inflight_call) == 1
        assert pf_inflight_call[0][0][1] == "pf-task-standalone"

    def test_report_page_shows_spinner_when_inflight(self, client):
        """When a portfolio analysis is in-flight, the report page renders
        the spinner instead of the stale report."""
        _set_elevated_user(client)
        client.post("/api/holdings/TEL", json={"shares": 500, "avg_cost": 20.0})

        # Seed a base report so /report/TEL renders.
        from ph_stocks_advisor.infra.config import get_repository

        repo = get_repository()
        report = FinalReport(
            symbol="TEL",
            verdict=Verdict.BUY,
            summary="Good stock.",
            price_section="P",
            dividend_section="D",
            movement_section="M",
            valuation_section="V",
            controversy_section="C",
        )
        repo.save(ReportRecord.from_final_report(report))

        # Seed a stale portfolio report.
        pr = PortfolioReportRecord(
            id=None,
            user_id="elevated@test.com",
            symbol="TEL",
            shares=500,
            avg_cost=20.0,
            analysis="Old stale analysis.",
            base_report_id=1,
        )
        repo.save_portfolio_report(pr)

        # Simulate an in-flight portfolio task via Redis.
        with patch("ph_stocks_advisor.web.app.get_redis") as mock_redis:
            mock_redis_inst = MagicMock()
            mock_redis.return_value = mock_redis_inst
            mock_redis_inst.get.return_value = "pf-task-inflight"

            resp = client.get("/report/TEL")

        html = resp.data.decode()
        # The spinner should be present.
        assert "portfolio-inline-progress" in html
        assert "Running personalised analysis" in html
        # The stale report content should NOT be present.
        assert "Old stale analysis" not in html
        # The inflight task ID should be exposed to JS.
        assert "pf-task-inflight" in html

    def test_report_page_shows_report_when_not_inflight(self, client):
        """When no portfolio analysis is in-flight, the report page renders
        the portfolio report normally."""
        _set_elevated_user(client)
        client.post("/api/holdings/TEL", json={"shares": 500, "avg_cost": 20.0})

        from ph_stocks_advisor.infra.config import get_repository

        repo = get_repository()
        report = FinalReport(
            symbol="TEL",
            verdict=Verdict.BUY,
            summary="Good stock.",
            price_section="P",
            dividend_section="D",
            movement_section="M",
            valuation_section="V",
            controversy_section="C",
        )
        repo.save(ReportRecord.from_final_report(report))

        pr = PortfolioReportRecord(
            id=None,
            user_id="elevated@test.com",
            symbol="TEL",
            shares=500,
            avg_cost=20.0,
            analysis="Fresh portfolio analysis content.",
            base_report_id=1,
        )
        repo.save_portfolio_report(pr)

        # Redis returns None (no inflight).
        with patch("ph_stocks_advisor.web.app.get_redis") as mock_redis:
            mock_redis_inst = MagicMock()
            mock_redis.return_value = mock_redis_inst
            mock_redis_inst.get.return_value = None

            resp = client.get("/report/TEL")

        html = resp.data.decode()
        # The report content should be present.
        assert "Fresh portfolio analysis content" in html
        # The spinner should NOT be present.
        assert "portfolio-inline-progress" not in html


def test_report_page_exposes_symbol_for_portfolio_js(client):
    """portfolio.js reads the symbol from #portfolio-btn's data-symbol —
    a stable contract (scraping the old heading broke with 404s when the
    report layout was redesigned)."""
    _set_elevated_user(client)

    from ph_stocks_advisor.data.models import FinalReport, Verdict
    from ph_stocks_advisor.infra.config import get_repository
    from ph_stocks_advisor.infra.repository import ReportRecord

    report = FinalReport(
        symbol="VREIT",
        verdict=Verdict.BUY,
        summary="Good stock.",
        price_section="ok",
        dividend_section="ok",
        movement_section="ok",
        valuation_section="ok",
        controversy_section="ok",
    )
    get_repository().save(ReportRecord.from_final_report(report))

    html = client.get("/report/VREIT").get_data(as_text=True)
    assert 'id="portfolio-btn"' in html
    assert 'data-symbol="VREIT"' in html
