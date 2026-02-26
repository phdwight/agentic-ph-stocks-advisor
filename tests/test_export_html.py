"""
Tests for the HTML export module.

Validates build_html() output, section parsing, escaping, verdict badge
rendering, and the standalone CLI entry point.
"""

from __future__ import annotations

from datetime import datetime, UTC
from unittest.mock import MagicMock, patch

import pytest

from ph_stocks_advisor.export_html import (
    build_html,
    _body_to_html,
    _esc,
    _md_bold_to_html,
    _parse_sections,
)
from ph_stocks_advisor.infra.repository import ReportRecord


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_SAMPLE_SUMMARY = """\
This is the executive overview.

**Company Overview:**
TEL is a leading telecom company.
- Strong market position
- Expanding into digital services

**Financial Health:**
Revenue growth is solid at **12%** year-over-year.

**Verdict:**
BUY — solid fundamentals.
"""


def _make_record(
    *,
    symbol: str = "TEL",
    verdict: str = "BUY",
    summary: str = _SAMPLE_SUMMARY,
    created_at: datetime | None = None,
) -> ReportRecord:
    return ReportRecord(
        id=42,
        symbol=symbol,
        verdict=verdict,
        summary=summary,
        price_section="Price looks healthy.",
        dividend_section="Dividends are good.",
        movement_section="Trending up.",
        valuation_section="Undervalued.",
        controversy_section="Minor risk.",
        created_at=created_at or datetime(2025, 7, 1, 10, 0, tzinfo=UTC),
    )


# ---------------------------------------------------------------------------
# _esc
# ---------------------------------------------------------------------------


class TestEsc:
    def test_escapes_html_entities(self):
        assert _esc("<script>alert('x')</script>") == (
            "&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;"
        )

    def test_escapes_quotes(self):
        assert "&quot;" in _esc('"hello"')


# ---------------------------------------------------------------------------
# _md_bold_to_html
# ---------------------------------------------------------------------------


class TestMdBoldToHtml:
    def test_converts_bold_markers(self):
        assert _md_bold_to_html("the **quick** fox") == "the <strong>quick</strong> fox"

    def test_multiple_bold_segments(self):
        result = _md_bold_to_html("**a** and **b**")
        assert result == "<strong>a</strong> and <strong>b</strong>"

    def test_no_bold_passes_through(self):
        assert _md_bold_to_html("plain text") == "plain text"


# ---------------------------------------------------------------------------
# _body_to_html
# ---------------------------------------------------------------------------


class TestBodyToHtml:
    def test_paragraph(self):
        html = _body_to_html("Hello world.")
        assert "<p>" in html
        assert "Hello world." in html

    def test_bullet_list(self):
        html = _body_to_html("- Item one\n- Item two")
        assert "<ul>" in html
        assert "<li>" in html
        assert "Item one" in html
        assert "Item two" in html

    def test_mixed_paragraphs_and_bullets(self):
        text = "Intro paragraph.\n\n- Bullet A\n- Bullet B\n\nClosing remark."
        html = _body_to_html(text)
        assert html.index("<p>Intro") < html.index("<ul>")
        assert html.index("</ul>") < html.index("Closing")

    def test_bold_inside_bullet(self):
        html = _body_to_html("- **strong** item")
        assert "<strong>strong</strong>" in html

    def test_asterisk_bullets(self):
        html = _body_to_html("* Item one\n* Item two")
        assert "<ul>" in html
        assert "Item one" in html


# ---------------------------------------------------------------------------
# _parse_sections
# ---------------------------------------------------------------------------


class TestParseSections:
    def test_parses_titled_sections(self):
        sections = _parse_sections(_SAMPLE_SUMMARY)
        titles = [t for t, _ in sections]
        assert "Executive Summary" in titles
        assert "Company Overview" in titles
        assert "Financial Health" in titles

    def test_verdict_section_captured(self):
        sections = _parse_sections(_SAMPLE_SUMMARY)
        titles = [t for t, _ in sections]
        assert "Verdict" in titles

    def test_executive_summary_first(self):
        sections = _parse_sections(_SAMPLE_SUMMARY)
        assert sections[0][0] == "Executive Summary"

    def test_section_body_not_empty(self):
        sections = _parse_sections(_SAMPLE_SUMMARY)
        for title, body in sections:
            assert body.strip(), f"Section '{title}' has empty body"

    def test_separator_lines_ignored(self):
        text = "**Intro:**\nHello\n---\n**Next:**\nWorld"
        sections = _parse_sections(text)
        for _, body in sections:
            assert "---" not in body

    def test_inline_heading_with_content(self):
        text = "**Rating:** Strong Buy signal."
        sections = _parse_sections(text)
        assert sections[0][0] == "Rating"
        assert "Strong Buy" in sections[0][1]


# ---------------------------------------------------------------------------
# build_html
# ---------------------------------------------------------------------------


class TestBuildHtml:
    def test_returns_string(self):
        record = _make_record()
        result = build_html(record)
        assert isinstance(result, str)

    def test_contains_doctype(self):
        html = build_html(_make_record())
        assert html.startswith("<!DOCTYPE html>")

    def test_contains_symbol_in_title(self):
        html = build_html(_make_record(symbol="SM"))
        assert "<title>SM Stock Analysis</title>" in html

    def test_buy_verdict_badge(self):
        html = build_html(_make_record(verdict="BUY"))
        assert 'class="badge buy"' in html
        assert "Verdict: BUY" in html

    def test_not_buy_verdict_badge(self):
        html = build_html(_make_record(verdict="NOT BUY"))
        assert 'class="badge not-buy"' in html
        assert "Verdict: NOT BUY" in html

    def test_excludes_verdict_section_from_body(self):
        html = build_html(_make_record())
        # The verdict section heading should not appear as an <h2>
        assert "<h2>Verdict</h2>" not in html

    def test_contains_section_headings(self):
        html = build_html(_make_record())
        assert "<h2>Company Overview</h2>" in html
        assert "<h2>Financial Health</h2>" in html

    def test_html_escaping_in_symbol(self):
        html = build_html(_make_record(symbol="<XSS>"))
        assert "&lt;XSS&gt;" in html
        assert "<XSS>" not in html.split("<style>")[0]  # not raw in header

    def test_generated_date_displayed(self):
        dt = datetime(2025, 7, 1, 10, 0, tzinfo=UTC)
        html = build_html(_make_record(created_at=dt))
        assert "July 01, 2025" in html

    def test_empty_summary_produces_valid_html(self):
        html = build_html(_make_record(summary=""))
        assert "<!DOCTYPE html>" in html
        assert "</html>" in html

    def test_contains_footer(self):
        html = build_html(_make_record())
        assert "Philippine Stock Advisor" in html


# ---------------------------------------------------------------------------
# CLI main()
# ---------------------------------------------------------------------------


class TestHtmlCli:
    def test_cli_writes_html_file(self, tmp_path):
        record = _make_record()
        mock_repo = MagicMock()
        mock_repo.get_latest_by_symbol.return_value = record

        out = tmp_path / "report.html"

        with patch(
            "ph_stocks_advisor.export_html.get_repository",
            return_value=mock_repo,
        ), patch(
            "sys.argv",
            ["export_html", "TEL", "-o", str(out)],
        ):
            from ph_stocks_advisor.export_html import main as html_main

            html_main()

        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in content
        assert "TEL Stock Analysis" in content

    def test_cli_exits_when_no_record(self):
        mock_repo = MagicMock()
        mock_repo.get_latest_by_symbol.return_value = None

        with patch(
            "ph_stocks_advisor.export_html.get_repository",
            return_value=mock_repo,
        ), patch(
            "sys.argv",
            ["export_html", "TEL"],
        ), pytest.raises(SystemExit) as exc_info:
            from ph_stocks_advisor.export_html import main as html_main

            html_main()

        assert exc_info.value.code == 1

    def test_cli_uses_specific_id(self, tmp_path):
        record = _make_record()
        mock_repo = MagicMock()
        mock_repo.get_by_id.return_value = record

        out = tmp_path / "report.html"

        with patch(
            "ph_stocks_advisor.export_html.get_repository",
            return_value=mock_repo,
        ), patch(
            "sys.argv",
            ["export_html", "TEL", "--id", "42", "-o", str(out)],
        ):
            from ph_stocks_advisor.export_html import main as html_main

            html_main()

        mock_repo.get_by_id.assert_called_once_with(42)
        assert out.exists()
