"""Tests for trading-session-aware cache freshness.

Two layers:
- Pure ``trading_calendar`` functions (deterministic via an injected ``now``).
- The ``/analyse`` market-open gate branches (they return early, so no
  redis/celery infra is needed). The market-*closed* → dispatch path is
  pre-existing behaviour gated only by an added early return, so it's not
  re-exercised here.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from ph_stocks_advisor.infra import trading_calendar as tc
from ph_stocks_advisor.infra.trading_calendar import PHT
from ph_stocks_advisor.web.app import create_app

# 2026-07-17 Fri · 07-20 Mon · 07-24 Fri · 07-25 Sat · 07-26 Sun · 07-27 Mon


def _pht(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=PHT)


# ---------------------------------------------------------------------------
# trading_calendar
# ---------------------------------------------------------------------------


def test_monday_morning_maps_to_friday_close():
    lc = tc.last_trading_close(_pht(2026, 7, 20, 8)).astimezone(PHT)
    assert lc.date() == date(2026, 7, 17) and lc.hour == 15


def test_after_close_uses_todays_close():
    lc = tc.last_trading_close(_pht(2026, 7, 20, 16)).astimezone(PHT)
    assert lc.date() == date(2026, 7, 20) and lc.hour == 15


def test_at_exactly_three_pm_uses_todays_close_and_market_closed():
    at_close = _pht(2026, 7, 20, 15)
    assert tc.last_trading_close(at_close).astimezone(PHT).date() == date(2026, 7, 20)
    assert tc.is_market_open(at_close) is False


@pytest.mark.parametrize("now", [_pht(2026, 7, 25, 10), _pht(2026, 7, 26, 9)])  # Sat, Sun
def test_weekend_maps_to_friday_close(now):
    assert tc.last_trading_close(now).astimezone(PHT).date() == date(2026, 7, 24)
    assert tc.is_market_open(now) is False


def test_market_open_only_during_session():
    assert tc.is_market_open(_pht(2026, 7, 20, 11)) is True  # Mon 11:00
    assert tc.is_market_open(_pht(2026, 7, 20, 8)) is False  # pre-open
    assert tc.is_market_open(_pht(2026, 7, 20, 9)) is True  # at open
    assert tc.is_market_open(_pht(2026, 7, 20, 14, 59)) is True  # just before close


def test_next_close_skips_weekend():
    fri_pm = _pht(2026, 7, 24, 16)
    nc = tc.next_trading_close(fri_pm).astimezone(PHT)
    assert nc.date() == date(2026, 7, 27) and nc.hour == 15  # Monday
    assert "Monday" in tc.next_close_label(fri_pm)


def test_naive_datetime_rejected():
    with pytest.raises(ValueError):
        tc.last_trading_close(datetime(2026, 7, 20, 8))  # noqa: DTZ001 — intentional


# ---------------------------------------------------------------------------
# /analyse market-open gate
# ---------------------------------------------------------------------------


@pytest.fixture
def analyse_client(monkeypatch, tmp_path):
    db = str(tmp_path / "th.db")
    monkeypatch.setenv("DB_BACKEND", "sqlite")
    monkeypatch.setenv("SQLITE_PATH", db)
    monkeypatch.setenv("WEBAUTHN_RP_ID", "")
    monkeypatch.delenv("ENTRA_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)

    import ph_stocks_advisor.infra.config as cfg

    cfg._reset_repository()
    cfg.get_settings.cache_clear()
    s = cfg.get_settings()
    s.db_backend = "sqlite"
    s.sqlite_path = db
    s.entra_client_id = ""
    s.google_client_id = ""
    s.webauthn_rp_id = ""  # auth disabled → anonymous access, CSRF skipped

    yield create_app().test_client()

    cfg._reset_repository()
    cfg.get_settings.cache_clear()


def _seed_report(symbol, created_at):
    from ph_stocks_advisor.infra.config import get_repository
    from ph_stocks_advisor.infra.repository import ReportRecord

    get_repository().save(
        ReportRecord(
            id=None,
            symbol=symbol,
            verdict="BUY",
            summary="**Executive Summary:**\nok",
            price_section="",
            dividend_section="",
            movement_section="",
            valuation_section="",
            controversy_section="",
            created_at=created_at,
        )
    )


def _patch_market(monkeypatch, *, is_open, last_close):
    import ph_stocks_advisor.web.app as appmod

    monkeypatch.setattr(appmod.trading_calendar, "is_market_open", lambda now=None: is_open)
    monkeypatch.setattr(appmod.trading_calendar, "last_trading_close", lambda now=None: last_close)
    monkeypatch.setattr(appmod.trading_calendar, "next_trading_close", lambda now=None: last_close + timedelta(days=1))
    monkeypatch.setattr(appmod.trading_calendar, "next_close_label", lambda now=None: "after today's 3:00 PM PHT close")


def test_fresh_report_served_from_cache(analyse_client, monkeypatch):
    close = datetime(2026, 7, 20, 7, tzinfo=UTC)
    _patch_market(monkeypatch, is_open=False, last_close=close)
    _seed_report("BDO", close + timedelta(hours=1))  # after close → fresh
    resp = analyse_client.post("/analyse", data={"symbol": "BDO"})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "cached"


def test_market_open_serves_stale_report(analyse_client, monkeypatch):
    close = datetime(2026, 7, 20, 7, tzinfo=UTC)
    _patch_market(monkeypatch, is_open=True, last_close=close)
    _seed_report("BDO", close - timedelta(days=2))  # before close → stale
    resp = analyse_client.post("/analyse", data={"symbol": "BDO"})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "cached"  # serves the stale one, no run


def test_market_open_no_report_blocks_until_close(analyse_client, monkeypatch):
    close = datetime(2026, 7, 20, 7, tzinfo=UTC)
    _patch_market(monkeypatch, is_open=True, last_close=close)
    resp = analyse_client.post("/analyse", data={"symbol": "NEWONE"})
    assert resp.status_code == 425
    body = resp.get_json()
    assert "market is open" in body["error"].lower()
    assert body["reset_at"] and body["symbol"] == "NEWONE"
