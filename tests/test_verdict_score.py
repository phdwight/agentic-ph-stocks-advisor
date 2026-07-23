"""Tests for the 0–100 verdict score scale.

Covers band mapping, the weighted per-dimension calculation (env-tunable
weights), verdict derivation from the score, fallbacks when sub-scores are
missing (structured and regex paths), repository persistence + migration,
and the report page's meter/word rendering (score vs legacy fallback).
"""

from __future__ import annotations

import sqlite3

import pytest

from ph_stocks_advisor.agents.consolidator import ConsolidatorAgent
from ph_stocks_advisor.data.models import (
    ConsolidationResponse,
    Verdict,
    score_band,
)
from tests.conftest import make_mock_llm, make_structured_mock_llm
from tests.dummy_responses import CONSOLIDATOR_BUY_RESPONSE


@pytest.fixture(autouse=True)
def _fresh_settings():
    """Give every test a clean Settings singleton (weights, threshold)."""
    from ph_stocks_advisor.infra.config import get_settings

    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Bands
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "score,band",
    [
        (0, "AVOID"),
        (19, "AVOID"),
        (20, "DON'T BUY"),
        (39, "DON'T BUY"),
        (40, "WAIT"),
        (59, "WAIT"),
        (60, "BUY"),
        (79, "BUY"),
        (80, "STRONG BUY"),
        (100, "STRONG BUY"),
    ],
)
def test_score_band_boundaries(score, band):
    assert score_band(score) == band


# ---------------------------------------------------------------------------
# Weighted calculation + verdict derivation
# ---------------------------------------------------------------------------


def _response(**scores) -> ConsolidationResponse:
    return ConsolidationResponse(
        verdict=scores.pop("verdict", Verdict.BUY),
        justification="Test.",
        summary=CONSOLIDATOR_BUY_RESPONSE,
        **scores,
    )


def test_weighted_score_uses_configured_weights(_fresh_settings):
    s = _fresh_settings
    s.score_weight_price = 0.5
    s.score_weight_valuation = 0.5
    s.score_weight_dividend = 0.0
    s.score_weight_movement = 0.0
    s.score_weight_controversy = 0.0
    s.score_weight_sentiment = 0.0

    resp = _response(price_score=90, valuation_score=50, dividend_score=0)
    # dividend has weight 0 → excluded; (90*0.5 + 50*0.5) / 1.0 = 70
    assert ConsolidatorAgent._weighted_score(resp) == 70


def test_weighted_score_skips_missing_dimensions_and_renormalises(_fresh_settings):
    # Defaults: valuation 0.25, others 0.15. Only price+valuation present
    # → (80*0.15 + 40*0.25) / 0.40 = 55
    resp = _response(price_score=80, valuation_score=40)
    assert ConsolidatorAgent._weighted_score(resp) == 55


def test_weighted_score_none_when_all_missing(_fresh_settings):
    assert ConsolidatorAgent._weighted_score(_response()) is None


def test_run_derives_verdict_from_score_overriding_llm(sample_advisor_state, _fresh_settings):
    # All six sub-scores at 45 → score 45 (< threshold 60) → NOT BUY even
    # though the LLM claimed BUY.
    resp = _response(
        verdict=Verdict.BUY,
        price_score=45,
        valuation_score=45,
        dividend_score=45,
        movement_score=45,
        controversy_score=45,
        sentiment_score=45,
    )
    report = ConsolidatorAgent(make_structured_mock_llm(resp)).run(sample_advisor_state)
    assert report.score == 45
    assert report.verdict == Verdict.NOT_BUY
    assert score_band(report.score) == "WAIT"


def test_run_respects_configurable_buy_threshold(sample_advisor_state, _fresh_settings):
    _fresh_settings.buy_score_threshold = 40
    resp = _response(
        verdict=Verdict.NOT_BUY,
        price_score=45,
        valuation_score=45,
        dividend_score=45,
        movement_score=45,
        controversy_score=45,
        sentiment_score=45,
    )
    report = ConsolidatorAgent(make_structured_mock_llm(resp)).run(sample_advisor_state)
    assert report.verdict == Verdict.BUY  # 45 >= 40


def test_structured_without_subscores_falls_back_to_verdict_score(sample_advisor_state):
    report = ConsolidatorAgent(make_structured_mock_llm(_response(verdict=Verdict.BUY))).run(sample_advisor_state)
    assert report.score == 75
    assert report.verdict == Verdict.BUY

    report = ConsolidatorAgent(make_structured_mock_llm(_response(verdict=Verdict.NOT_BUY))).run(sample_advisor_state)
    assert report.score == 25
    assert report.verdict == Verdict.NOT_BUY


def test_regex_fallback_derives_score(sample_advisor_state):
    report = ConsolidatorAgent(make_mock_llm("Analysis...\n**Verdict: NOT BUY**")).run(sample_advisor_state)
    assert report.verdict == Verdict.NOT_BUY
    assert report.score == 25


# ---------------------------------------------------------------------------
# Repository persistence + migration
# ---------------------------------------------------------------------------


def _record(score):
    from ph_stocks_advisor.infra.repository import ReportRecord

    return ReportRecord(
        id=None,
        symbol="TEL",
        verdict="BUY",
        summary="s",
        price_section="",
        dividend_section="",
        movement_section="",
        valuation_section="",
        controversy_section="",
        score=score,
    )


def test_sqlite_score_roundtrip(tmp_path):
    from ph_stocks_advisor.infra.repository_sqlite import SQLiteReportRepository

    repo = SQLiteReportRepository(str(tmp_path / "s.db"))
    repo.initialize()
    rid = repo.save(_record(score=72))
    got = repo.get_by_id(rid)
    assert got is not None and got.score == 72

    rid_none = repo.save(_record(score=None))
    got_none = repo.get_by_id(rid_none)
    assert got_none is not None and got_none.score is None


def test_sqlite_migration_adds_score_to_legacy_db(tmp_path):
    """A pre-scoring reports table gains the score column on initialize()."""
    db = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(db)
    conn.execute(
        """
        CREATE TABLE reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL, verdict TEXT NOT NULL, summary TEXT NOT NULL,
            price_section TEXT NOT NULL DEFAULT '', dividend_section TEXT NOT NULL DEFAULT '',
            movement_section TEXT NOT NULL DEFAULT '', valuation_section TEXT NOT NULL DEFAULT '',
            controversy_section TEXT NOT NULL DEFAULT '', sentiment_section TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO reports (symbol, verdict, summary, created_at) "
        "VALUES ('BDO', 'BUY', 's', '2026-01-05T00:00:00+00:00')"
    )
    conn.commit()
    conn.close()

    from ph_stocks_advisor.infra.repository_sqlite import SQLiteReportRepository

    repo = SQLiteReportRepository(db)
    repo.initialize()  # migration adds the column
    repo.initialize()  # idempotent
    legacy = repo.get_latest_by_symbol("BDO")
    assert legacy is not None and legacy.score is None
    rid = repo.save(_record(score=63))
    assert repo.get_by_id(rid).score == 63  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Report page rendering
# ---------------------------------------------------------------------------


@pytest.fixture
def page_client(monkeypatch, tmp_path):
    from ph_stocks_advisor.web.app import create_app

    db = str(tmp_path / "web.db")
    monkeypatch.setenv("DB_BACKEND", "sqlite")
    monkeypatch.setenv("SQLITE_PATH", db)
    monkeypatch.delenv("ENTRA_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.setenv("WEBAUTHN_RP_ID", "")

    import ph_stocks_advisor.infra.config as cfg

    cfg._reset_repository()
    cfg.get_settings.cache_clear()
    s = cfg.get_settings()
    s.db_backend = "sqlite"
    s.sqlite_path = db
    s.entra_client_id = ""
    s.google_client_id = ""
    s.webauthn_rp_id = ""

    yield create_app().test_client()

    cfg._reset_repository()
    cfg.get_settings.cache_clear()


def test_report_page_marker_at_score(page_client):
    from ph_stocks_advisor.infra.config import get_repository

    get_repository().save(_record(score=72))
    html = page_client.get("/report/TEL").get_data(as_text=True)
    assert "left: 72%" in html
    assert "BUY" in html  # 72 → BUY band
    assert "score / 100" in html


def test_report_page_legacy_fallback_marker(page_client):
    from ph_stocks_advisor.infra.config import get_repository

    get_repository().save(_record(score=None))
    html = page_client.get("/report/TEL").get_data(as_text=True)
    assert "left: 80%" in html  # legacy BUY fallback
    assert "score / 100" not in html  # no number without a score


# ---------------------------------------------------------------------------
# Inline verdict lines are stripped from section bodies
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "verdict_line",
    [
        "Verdict: NOT BUY",
        "Verdict: BUY",
        "**Verdict: NOT BUY**",
        "**Verdict:** NOT BUY",
        "Final Verdict: NOT BUY",
        "verdict: not buy.",
    ],
)
def test_parse_sections_strips_inline_verdict_lines(verdict_line):
    """The verdict renders once (panel/score scale) — never inside bodies."""
    from ph_stocks_advisor.export.formatter import parse_sections

    summary = (
        "**Executive Summary:**\nGood stock overall.\n\n"
        "**Sentiment Analysis:**\n- Neutral outlook.\n"
        "The dividend case is strong, but the price is unsupported.\n"
        f"{verdict_line}\n"
    )
    sections = parse_sections(summary)
    joined = "\n".join(body for _, body in sections)
    assert "Verdict" not in joined
    # Real content around it survives.
    assert "Neutral outlook" in joined
    assert "dividend case is strong" in joined


def test_parse_sections_keeps_verdict_mentions_inside_prose():
    """Only standalone verdict lines are dropped, not prose mentioning them."""
    from ph_stocks_advisor.export.formatter import parse_sections

    summary = "**Executive Summary:**\nAnalysts debate whether the verdict: BUY calls were premature.\n"
    sections = parse_sections(summary)
    assert "verdict: BUY calls were premature" in sections[0][1]


def test_report_page_has_no_inline_verdict(page_client):
    from ph_stocks_advisor.infra.config import get_repository
    from ph_stocks_advisor.infra.repository import ReportRecord

    get_repository().save(
        ReportRecord(
            id=None,
            symbol="TEL",
            verdict="NOT BUY",
            summary=("**Executive Summary:**\nWeak setup.\n\n**Sentiment Analysis:**\n- Neutral.\nVerdict: NOT BUY\n"),
            price_section="",
            dividend_section="",
            movement_section="",
            valuation_section="",
            controversy_section="",
            score=43,
        )
    )
    html = page_client.get("/report/TEL").get_data(as_text=True)
    assert "WAIT" in html  # panel shows the band for 43
    assert "Verdict: NOT BUY" not in html  # no inline contradiction


# ---------------------------------------------------------------------------
# Verdict chips (sidebar / marquee / history) show the score band
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "score,verdict,label,cls",
    [
        (85, "BUY", "STRONG BUY", "buy"),
        (65, "BUY", "BUY", "buy"),
        (43, "NOT BUY", "WAIT", "wait"),
        (25, "NOT BUY", "DON'T BUY", "avoid"),
        (10, "NOT BUY", "AVOID", "avoid"),
        (None, "BUY", "BUY", "buy"),  # legacy rows keep the buy label
        (None, "NOT BUY", "DON'T BUY", "not-buy"),  # legacy: friendly label, old style
    ],
)
def test_verdict_chip_prefers_score_band(score, verdict, label, cls):
    from ph_stocks_advisor.web.app import _verdict_chip

    chip = _verdict_chip(_record_with(score=score, verdict=verdict))
    assert chip == {"label": label, "cls": cls}


def _record_with(score, verdict):
    from ph_stocks_advisor.infra.repository import ReportRecord

    return ReportRecord(
        id=None,
        symbol="TEL",
        verdict=verdict,
        summary="s",
        price_section="",
        dividend_section="",
        movement_section="",
        valuation_section="",
        controversy_section="",
        score=score,
    )


def test_homepage_marquee_shows_band_not_binary(page_client):
    """A WAIT-scored stock must not appear as NOT BUY on the homepage."""
    from ph_stocks_advisor.infra.config import get_repository

    repo = get_repository()
    repo.save(_record_with(score=43, verdict="NOT BUY"))
    # Auth-disabled requests run as the dev user; link the symbol to them
    # so the homepage "Your Analysed Stocks" marquee includes it.
    repo.add_user_symbol("dev@localhost", "TEL")
    html = page_client.get("/").get_data(as_text=True)
    assert ">WAIT</span>" in html
    assert ">NOT BUY</span>" not in html


def test_history_page_shows_band(page_client):
    from ph_stocks_advisor.infra.config import get_repository

    get_repository().save(_record_with(score=43, verdict="NOT BUY"))
    html = page_client.get("/history/TEL").get_data(as_text=True)
    assert "WAIT" in html


def test_static_assets_are_content_hash_versioned(page_client):
    """Asset URLs carry a content hash, not the app version (which only
    changes on GitHub Releases and let CDNs serve stale CSS/JS)."""
    import re

    html = page_client.get("/").get_data(as_text=True)
    m = re.search(r"style\.css\?v=([0-9a-f]{10})", html)
    assert m, "style.css must be versioned with a 10-char content hash"
    assert re.search(r"app\.js\?v=" + m.group(1), html)  # same rev everywhere


def test_gap_dimensions_are_excluded_from_score_even_if_llm_scored_them(_fresh_settings):
    """A dimension in data_gaps is force-nulled before weighting — the LLM
    can't sneak a number in for data that doesn't exist."""
    from ph_stocks_advisor.data.models import AdvisorState

    resp = _response(
        verdict=Verdict.BUY,
        price_score=40,
        valuation_score=40,
        dividend_score=100,  # LLM wrongly scored the missing dimension
        movement_score=40,
        controversy_score=40,
        sentiment_score=40,
    )
    state = AdvisorState(symbol="DITO", data_gaps=["dividend_analysis"])
    report = ConsolidatorAgent(make_structured_mock_llm(resp)).run(state)
    # dividend excluded -> all remaining sub-scores are 40 -> score 40 (WAIT),
    # not 49 (which would include the bogus dividend 100).
    assert report.score == 40
    assert score_band(report.score) == "WAIT"


@pytest.mark.parametrize(
    "heading",
    [
        "**NOT BUY**",
        "**BUY**",
        "**DON'T BUY**",
        "**WAIT**",
        "**AVOID**",
        "**STRONG BUY**",
        "**Verdict**",
        "**Final Verdict:**",
        "### NOT BUY",
    ],
)
def test_verdict_shaped_headings_never_become_sections(heading):
    """The LLM sometimes emits the verdict as its own heading followed by a
    justification. That would render as an extra card contradicting the score
    band (a WAIT panel beside a "NOT BUY" card), so such sections are dropped."""
    from ph_stocks_advisor.export.formatter import parse_sections

    summary = (
        "**Executive Summary:**\nSolid but pricey.\n\n"
        "**Price Analysis:**\n- Near the 52-week high.\n\n"
        f"{heading}\nJustification: computed without the dividend dimension.\n"
    )
    titles = [t for t, _ in parse_sections(summary)]
    # The heading is retitled (never a verdict label) but the reasoning is kept.
    assert titles == ["Executive Summary", "Price Analysis", "Why This Verdict"]


def test_real_analysis_sections_are_not_dropped():
    """The verdict-title filter must not eat legitimate sections."""
    from ph_stocks_advisor.export.formatter import parse_sections

    summary = (
        "**Executive Summary:**\nok\n\n"
        "**Dividend Analysis:**\n- yield 5%\n\n"
        "**Sentiment / Global Events Analysis:**\n- neutral\n"
    )
    titles = [t for t, _ in parse_sections(summary)]
    assert titles == [
        "Executive Summary",
        "Dividend Analysis",
        "Sentiment / Global Events Analysis",
    ]


def test_verdict_heading_body_is_kept_and_neutralised():
    """The justification carries the data-gap explanation, so it survives —
    with the stale verdict label rewritten to a neutral subject."""
    from ph_stocks_advisor.export.formatter import parse_sections

    summary = (
        "**Executive Summary:**\nWeak setup.\n\n"
        "**NOT BUY**\nJustification: NOT BUY was computed without the "
        "unavailable dividend dimension, so there is no income case.\n"
    )
    sections = dict(parse_sections(summary))
    body = sections["Why This Verdict"]
    assert body.startswith("This assessment was computed without")
    assert "NOT BUY" not in body
    assert "dividend dimension" in body


def test_empty_verdict_heading_is_dropped_entirely():
    from ph_stocks_advisor.export.formatter import parse_sections

    titles = [t for t, _ in parse_sections("**Executive Summary:**\nok\n\n**NOT BUY**\n")]
    assert titles == ["Executive Summary"]


def test_leading_document_title_is_dropped():
    """The consolidator (esp. Anthropic) sometimes prepends a report title
    before the Executive Summary; it parses as an empty-body heading and would
    render as a spurious 'Market mood' card. Empty-body sections are dropped."""
    from ph_stocks_advisor.export.formatter import parse_sections

    summary = (
        "**BDO Unibank — Investment Report (as of 2026-07-22)**\n\n"
        "**Executive Summary:**\nBDO is trading at P122.90.\n\n"
        "**Price Analysis:**\n- Near the low end of its range.\n"
    )
    titles = [t for t, _ in parse_sections(summary)]
    assert "BDO Unibank — Investment Report (as of 2026-07-22)" not in titles
    assert titles == ["Executive Summary", "Price Analysis"]


def test_real_sections_with_content_are_never_dropped():
    from ph_stocks_advisor.export.formatter import parse_sections

    summary = (
        "**Executive Summary:**\nok\n\n"
        "**Dividend Analysis:**\n- yield 5%\n\n"
        "**Sentiment / Global Events Analysis:**\n- neutral\n"
    )
    assert [t for t, _ in parse_sections(summary)] == [
        "Executive Summary",
        "Dividend Analysis",
        "Sentiment / Global Events Analysis",
    ]


def test_verdict_panel_states_new_position_scope(page_client):
    """The verdict explicitly says it assumes a NEW position (an external
    review flagged that a generic BUY reads as advice for existing holders)."""
    from ph_stocks_advisor.infra.config import get_repository

    get_repository().save(_record(score=63))
    html = page_client.get("/report/TEL").get_data(as_text=True)
    assert "Assumes a new position" in html
