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
def sqlite_repo(tmp_path) -> SQLiteReportRepository:
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
        resp = client.post("/api/portfolio-analyse/TEL")
        assert resp.status_code == 400
        assert "No holding found" in resp.get_json()["error"]


# ---------------------------------------------------------------------------
# Portfolio analysis daily cooldown
# ---------------------------------------------------------------------------


class TestPortfolioCooldown:
    """Portfolio analysis can only run once per stock per day (resets at 8 AM PHT / midnight UTC)."""

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

        # Manually backdate the created_at to yesterday.
        import sqlite3

        conn = sqlite3.connect(repo._db_path)
        yesterday = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        conn.execute(
            "UPDATE portfolio_reports SET created_at = ? WHERE user_id = ? AND symbol = ?",
            (yesterday, "elevated@test.com", "TEL"),
        )
        conn.commit()
        conn.close()

        # Now the cooldown should have passed — the endpoint should accept (needs Celery mock).
        with patch("ph_stocks_advisor.web.tasks.portfolio_analyse_stock") as mock_task:
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
        resp = client.post("/api/portfolio-analyse/TEL")
        assert resp.status_code == 429

        # SM should be allowed.
        with patch("ph_stocks_advisor.web.tasks.portfolio_analyse_stock") as mock_task:
            mock_result = MagicMock()
            mock_result.id = "task-sm"
            mock_task.delay.return_value = mock_result

            resp = client.post("/api/portfolio-analyse/SM")
            assert resp.status_code == 200
            assert resp.get_json()["status"] == "started"
