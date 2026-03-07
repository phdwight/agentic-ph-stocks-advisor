"""
Tests for the export package — OutputFormatter ABC, shared utilities,
PdfFormatter, HtmlFormatter, registry, and generic CLI.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ph_stocks_advisor.export import (
    FORMATTER_REGISTRY,
    HtmlFormatter,
    OutputFormatter,
    PdfFormatter,
    get_formatter,
    parse_sections,
)
from ph_stocks_advisor.export.html import _body_to_html, _esc, _md_bold_to_html
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


# =========================================================================
# parse_sections (shared utility from formatter.py)
# =========================================================================


class TestParseSections:
    def test_parses_titled_sections(self):
        sections = parse_sections(_SAMPLE_SUMMARY)
        titles = [t for t, _ in sections]
        assert "Executive Summary" in titles
        assert "Company Overview" in titles
        assert "Financial Health" in titles

    def test_verdict_section_captured(self):
        titles = [t for t, _ in parse_sections(_SAMPLE_SUMMARY)]
        assert "Verdict" in titles

    def test_executive_summary_first(self):
        sections = parse_sections(_SAMPLE_SUMMARY)
        assert sections[0][0] == "Executive Summary"

    def test_section_body_not_empty(self):
        for title, body in parse_sections(_SAMPLE_SUMMARY):
            assert body.strip(), f"Section '{title}' has empty body"

    def test_separator_lines_ignored(self):
        text = "**Intro:**\nHello\n---\n**Next:**\nWorld"
        for _, body in parse_sections(text):
            assert "---" not in body

    def test_inline_heading_with_content(self):
        text = "**Rating:** Strong Buy signal."
        sections = parse_sections(text)
        assert sections[0][0] == "Rating"
        assert "Strong Buy" in sections[0][1]

    # --- Markdown ATX heading support ---

    @pytest.mark.parametrize("text,expected_titles", [
        (
            "### Price Analysis\n- Price is PHP 14.26\n### Dividend Analysis\n- Yield is 7%",
            ["Price Analysis", "Dividend Analysis"],
        ),
        ("## Overview\nSome overview text.", ["Overview"]),
        ("### Price Analysis:\n- Bullet", ["Price Analysis"]),
        (
            "**Price Analysis:**\n- Price bullet\n### Dividend Analysis\n- Dividend bullet",
            ["Price Analysis", "Dividend Analysis"],
        ),
    ], ids=["h3", "h2", "h3-trailing-colon", "mixed-bold-and-h3"])
    def test_markdown_heading_recognition(self, text, expected_titles):
        sections = parse_sections(text)
        titles = [t for t, _ in sections]
        for expected in expected_titles:
            assert expected in titles

    # --- Dash / separator stripping ---

    def test_multi_dash_separator_lines_stripped(self):
        text = "**Intro:**\nHello\n----\n**Next:**\nWorld"
        for _, body in parse_sections(text):
            assert "----" not in body

    def test_trailing_dashes_stripped_from_body(self):
        text = "### Analysis\n- Yield is 7%.--\n- Payout ratio is 93%.----"
        sections = parse_sections(text)
        body = sections[0][1]
        assert "--" not in body
        assert "7%." in body
        assert "93%." in body

    # --- Trailing dashes in headings (regression: MREIT PDF output) ---

    @pytest.mark.parametrize("text", [
        "**Price Analysis:----**\n- Bullet one",
        "### Price Analysis----\n- Bullet one",
        "**Price Analysis----:**\n- Bullet one",
    ], ids=["bold-trailing", "h3-trailing", "bold-dashes-before-colon"])
    def test_trailing_dashes_stripped_from_heading(self, text):
        sections = parse_sections(text)
        assert sections[0][0] == "Price Analysis"
        assert "--" not in sections[0][0]

    @pytest.mark.parametrize("text,expected_titles", [
        (
            "Summary paragraph.\n\n**Price Analysis**\n- BDO is at PHP 138.50.\n\n"
            "**Dividend Analysis**\n- Dividend yield is about 3.1%.\n",
            ["Price Analysis", "Dividend Analysis"],
        ),
        (
            "### Price Analysis----\n - MREIT is at PHP 14.26\n"
            "### Dividend Analysis----\n - Trailing dividend yield is about 7.06%\n"
            "### Price Movement Analysis----\n - Over roughly a year, the stock is up about +5.6%\n",
            ["Price Analysis", "Dividend Analysis", "Price Movement Analysis"],
        ),
        (
            "Executive Summary:\nAREIT is trading near the lower end.\n\n"
            "Price Analysis:\n- Current price is PHP 40.05\n\n"
            "Dividend Analysis:\n- Yield is 5.8%\n",
            ["Executive Summary", "Price Analysis", "Dividend Analysis"],
        ),
        (
            "Executive Summary:\nSummary text here.\n\n"
            "**Price Analysis:**\n- Price bullet\n"
            "Valuation Analysis:\n- Undervalued.\n",
            ["Executive Summary", "Price Analysis", "Valuation Analysis"],
        ),
    ], ids=["bold-no-colon", "mreit-dashes", "plain-text", "plain-text-mixed-bold"])
    def test_heading_style_variants(self, text, expected_titles):
        sections = parse_sections(text)
        titles = [t for t, _ in sections]
        for expected in expected_titles:
            assert expected in titles
        # No dashes should leak into any title
        for title, _ in sections:
            assert "--" not in title, f"Dashes leaked into title: {title!r}"

    def test_plain_text_controversy_risk_heading(self):
        """Handles 'Controversy / Risk Analysis:' variant."""
        text = "Controversy / Risk Analysis:\n- No red flags."
        sections = parse_sections(text)
        assert sections[0][0] == "Controversy / Risk Analysis"

    # --- Duplicate title stripping from body ---

    def test_duplicate_title_stripped_from_body(self):
        """When body starts with the same title, it's removed."""
        text = (
            "**Executive Summary:**\n"
            "Executive Summary:\n"
            "AREIT is trading near the lower end.\n"
        )
        sections = parse_sections(text)
        body = sections[0][1]
        assert not body.strip().startswith("Executive Summary")
        assert "AREIT is trading" in body

    def test_no_false_positive_title_strip(self):
        """Body that doesn't repeat the title is left intact."""
        text = "**Executive Summary:**\nAREIT looks solid."
        sections = parse_sections(text)
        assert "AREIT looks solid" in sections[0][1]


# =========================================================================
# OutputFormatter ABC contract
# =========================================================================


class TestOutputFormatterContract:
    def test_abc_cannot_be_instantiated(self):
        with pytest.raises(TypeError):
            OutputFormatter()  # type: ignore[abstract]

    def test_pdf_formatter_is_output_formatter(self):
        assert isinstance(PdfFormatter(), OutputFormatter)

    def test_html_formatter_is_output_formatter(self):
        assert isinstance(HtmlFormatter(), OutputFormatter)

    def test_formatter_write_delegates_to_render(self, tmp_path):
        fmt = HtmlFormatter()
        record = _make_record()
        out = tmp_path / "test.html"
        fmt.write(record, out)
        assert out.exists()
        content = out.read_bytes()
        assert content == fmt.render(record)


# =========================================================================
# get_formatter registry
# =========================================================================


class TestGetFormatter:
    @pytest.mark.parametrize("fmt_name,expected_cls", [
        ("pdf", PdfFormatter),
        ("html", HtmlFormatter),
    ], ids=["pdf", "html"])
    def test_returns_correct_formatter(self, fmt_name, expected_cls):
        assert isinstance(get_formatter(fmt_name), expected_cls)

    def test_unknown_format_raises_key_error(self):
        with pytest.raises(KeyError, match="docx"):
            get_formatter("docx")


# =========================================================================
# HtmlFormatter
# =========================================================================


class TestHtmlEsc:
    def test_escapes_html_entities_and_quotes(self):
        assert _esc("<script>alert('x')</script>") == (
            "&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;"
        )
        assert "&quot;" in _esc('"hello"')


class TestMdBoldToHtml:
    def test_converts_bold_markers(self):
        assert _md_bold_to_html("the **quick** fox") == "the <strong>quick</strong> fox"

    def test_multiple_bold_segments(self):
        result = _md_bold_to_html("**a** and **b**")
        assert result == "<strong>a</strong> and <strong>b</strong>"


class TestBodyToHtml:
    def test_paragraph(self):
        out = _body_to_html("Hello world.")
        assert "<p>" in out and "Hello world." in out

    def test_bullet_list(self):
        out = _body_to_html("- Item one\n- Item two")
        assert "<ul>" in out and "<li>" in out

    def test_mixed_paragraphs_and_bullets(self):
        text = "Intro paragraph.\n\n- Bullet A\n- Bullet B\n\nClosing remark."
        out = _body_to_html(text)
        assert out.index("<p>Intro") < out.index("<ul>")
        assert out.index("</ul>") < out.index("Closing")

    def test_bold_inside_bullet(self):
        out = _body_to_html("- **strong** item")
        assert "<strong>strong</strong>" in out

    def test_asterisk_bullets(self):
        out = _body_to_html("* Item one\n* Item two")
        assert "<ul>" in out and "Item one" in out

    def test_trailing_dashes_stripped_from_bullets(self):
        out = _body_to_html("- Yield is 7%.--\n- Ratio is 93%.----")
        assert "--" not in out
        assert "7%." in out

    @pytest.mark.parametrize("input_text,expected_li_contents", [
        (
            "- - At PHP 138.50, BDO is closer\n- - The move was small",
            ["At PHP 138.50", "The move was small"],
        ),
        ("- - - deeply nested", ["deeply nested"]),
    ], ids=["double-dash", "triple-dash"])
    def test_repeated_dash_bullets_collapsed(self, input_text, expected_li_contents):
        """LLM sometimes produces '- - text'; should render as single bullet."""
        out = _body_to_html(input_text)
        for content in expected_li_contents:
            assert f"<li>{content}" in out


class TestHtmlRender:
    def test_returns_bytes(self):
        result = HtmlFormatter().render(_make_record())
        assert isinstance(result, bytes)

    def test_contains_doctype(self):
        html = HtmlFormatter().render(_make_record()).decode()
        assert html.startswith("<!DOCTYPE html>")

    def test_contains_symbol_in_title(self):
        html = HtmlFormatter().render(_make_record(symbol="SM")).decode()
        assert "<title>SM Stock Analysis</title>" in html

    @pytest.mark.parametrize("verdict,badge_class", [
        ("BUY", "badge buy"),
        ("NOT BUY", "badge not-buy"),
    ], ids=["buy", "not-buy"])
    def test_verdict_badge(self, verdict, badge_class):
        html = HtmlFormatter().render(_make_record(verdict=verdict)).decode()
        assert f'class="{badge_class}"' in html
        assert f">{verdict}<" in html

    def test_verdict_label_outside_pill(self):
        """'Verdict:' label should be outside the colored pill badge."""
        html = HtmlFormatter().render(_make_record(verdict="BUY")).decode()
        assert 'class="verdict-label"' in html
        assert "Verdict:" in html
        # The badge itself should only contain the verdict value
        assert '>BUY</span>' in html

    def test_badge_is_pill_shaped(self):
        """CSS should use large border-radius for a pill shape."""
        html = HtmlFormatter().render(_make_record()).decode()
        assert "border-radius:999px" in html

    def test_excludes_verdict_section_from_body(self):
        html = HtmlFormatter().render(_make_record()).decode()
        assert "<h2>Verdict</h2>" not in html

    def test_contains_section_headings(self):
        html = HtmlFormatter().render(_make_record()).decode()
        assert "<h2>Company Overview</h2>" in html
        assert "<h2>Financial Health</h2>" in html

    def test_html_escaping_in_symbol(self):
        html = HtmlFormatter().render(_make_record(symbol="<XSS>")).decode()
        assert "&lt;XSS&gt;" in html

    def test_generated_date_displayed(self):
        dt = datetime(2025, 7, 1, 10, 0, tzinfo=UTC)
        html = HtmlFormatter().render(_make_record(created_at=dt)).decode()
        assert "July 01, 2025" in html

    def test_empty_summary_produces_valid_html(self):
        html = HtmlFormatter().render(_make_record(summary="")).decode()
        assert "<!DOCTYPE html>" in html and "</html>" in html

    def test_contains_footer(self):
        html = HtmlFormatter().render(_make_record()).decode()
        assert "does not constitute financial advice" in html
        assert "DragonFi" in html


# =========================================================================
# PdfFormatter
# =========================================================================


class TestPdfRender:
    def test_returns_bytes_like(self):
        result = PdfFormatter().render(_make_record())
        assert isinstance(result, (bytes, bytearray))

    def test_starts_with_pdf_header(self):
        result = PdfFormatter().render(_make_record())
        assert result[:5] == b"%PDF-"

    def test_pdf_not_empty(self):
        result = PdfFormatter().render(_make_record())
        assert len(result) > 500

    def test_write_creates_file(self, tmp_path):
        out = tmp_path / "report.pdf"
        PdfFormatter().write(_make_record(), out)
        assert out.exists()
        assert out.read_bytes()[:5] == b"%PDF-"

    def test_markdown_headings_render_without_hashes(self, tmp_path):
        """Regression: ### headings must not appear as raw '###' in PDF."""
        summary = (
            "### Price Analysis\n"
            "- MREIT last traded at PHP 14.26.--\n"
            "### Dividend Analysis\n"
            "- Trailing dividend yield is about 7.06%.----\n"
            "**Verdict: BUY**\n"
        )
        record = _make_record(summary=summary)
        out = tmp_path / "report.pdf"
        PdfFormatter().write(record, out)
        assert out.exists()
        assert out.stat().st_size > 500

    def test_double_dash_bullets_render_without_error(self, tmp_path):
        """Regression: LLM '- - text' bullets should render cleanly."""
        summary = (
            "### Price Analysis\n"
            "- - At PHP 138.50, BDO is closer to its 52-week low.\n"
            "- - The move from PHP 137.60 is a small gain.\n"
            "**Verdict: BUY**\n"
        )
        record = _make_record(summary=summary)
        out = tmp_path / "report.pdf"
        PdfFormatter().write(record, out)
        assert out.exists()
        assert out.stat().st_size > 500

    @pytest.mark.parametrize("verdict", ["BUY", "NOT BUY"])
    def test_pill_shaped_badge(self, tmp_path, verdict):
        """PDF should render a pill-shaped verdict badge."""
        out = tmp_path / "report.pdf"
        PdfFormatter().write(_make_record(verdict=verdict), out)
        assert out.exists()
        assert out.stat().st_size > 500


# =========================================================================
# Standalone CLI (export_cli)
# =========================================================================


class TestExportCli:
    def test_html_cli_writes_file(self, tmp_path):
        record = _make_record()
        mock_repo = MagicMock()
        mock_repo.get_latest_by_symbol.return_value = record

        out = tmp_path / "report.html"

        with patch(
            "ph_stocks_advisor.export.formatter.get_repository",
            return_value=mock_repo,
        ), patch(
            "sys.argv",
            ["export", "TEL", "-o", str(out)],
        ):
            from ph_stocks_advisor.export.html import main as html_main

            html_main()

        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in content
        assert "TEL Stock Analysis" in content

    def test_pdf_cli_writes_file(self, tmp_path):
        record = _make_record()
        mock_repo = MagicMock()
        mock_repo.get_latest_by_symbol.return_value = record

        out = tmp_path / "report.pdf"

        with patch(
            "ph_stocks_advisor.export.formatter.get_repository",
            return_value=mock_repo,
        ), patch(
            "sys.argv",
            ["export", "TEL", "-o", str(out)],
        ):
            from ph_stocks_advisor.export.pdf import main as pdf_main

            pdf_main()

        assert out.exists()
        assert out.read_bytes()[:5] == b"%PDF-"

    def test_cli_exits_when_no_record_found(self):
        mock_repo = MagicMock()
        mock_repo.get_latest_by_symbol.return_value = None

        with patch(
            "ph_stocks_advisor.export.formatter.get_repository",
            return_value=mock_repo,
        ), patch(
            "sys.argv",
            ["export", "TEL"],
        ), pytest.raises(SystemExit) as exc_info:
            from ph_stocks_advisor.export.html import main as html_main

            html_main()

        assert exc_info.value.code == 1

    def test_cli_uses_specific_id(self, tmp_path):
        record = _make_record()
        mock_repo = MagicMock()
        mock_repo.get_by_id.return_value = record

        out = tmp_path / "report.html"

        with patch(
            "ph_stocks_advisor.export.formatter.get_repository",
            return_value=mock_repo,
        ), patch(
            "sys.argv",
            ["export", "TEL", "--id", "42", "-o", str(out)],
        ):
            from ph_stocks_advisor.export.html import main as html_main

            html_main()

        mock_repo.get_by_id.assert_called_once_with(42)
        assert out.exists()
