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
    def test_returns_pdf_formatter(self):
        assert isinstance(get_formatter("pdf"), PdfFormatter)

    def test_returns_html_formatter(self):
        assert isinstance(get_formatter("html"), HtmlFormatter)

    def test_unknown_format_raises_key_error(self):
        with pytest.raises(KeyError, match="docx"):
            get_formatter("docx")

    def test_registry_contains_expected_formats(self):
        assert "pdf" in FORMATTER_REGISTRY
        assert "html" in FORMATTER_REGISTRY


# =========================================================================
# HtmlFormatter
# =========================================================================


class TestHtmlFormatterProperties:
    def test_file_extension(self):
        assert HtmlFormatter().file_extension == ".html"

    def test_format_label(self):
        assert HtmlFormatter().format_label == "HTML"

    def test_emoji(self):
        assert HtmlFormatter().emoji == "🌐"


class TestHtmlEsc:
    def test_escapes_html_entities(self):
        assert _esc("<script>alert('x')</script>") == (
            "&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;"
        )

    def test_escapes_quotes(self):
        assert "&quot;" in _esc('"hello"')


class TestMdBoldToHtml:
    def test_converts_bold_markers(self):
        assert _md_bold_to_html("the **quick** fox") == "the <strong>quick</strong> fox"

    def test_multiple_bold_segments(self):
        result = _md_bold_to_html("**a** and **b**")
        assert result == "<strong>a</strong> and <strong>b</strong>"

    def test_no_bold_passes_through(self):
        assert _md_bold_to_html("plain text") == "plain text"


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

    def test_buy_verdict_badge(self):
        html = HtmlFormatter().render(_make_record(verdict="BUY")).decode()
        assert 'class="badge buy"' in html
        assert "Verdict: BUY" in html

    def test_not_buy_verdict_badge(self):
        html = HtmlFormatter().render(_make_record(verdict="NOT BUY")).decode()
        assert 'class="badge not-buy"' in html

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
        assert "Philippine Stock Advisor" in html


# =========================================================================
# PdfFormatter
# =========================================================================


class TestPdfFormatterProperties:
    def test_file_extension(self):
        assert PdfFormatter().file_extension == ".pdf"

    def test_format_label(self):
        assert PdfFormatter().format_label == "PDF"

    def test_emoji(self):
        assert PdfFormatter().emoji == "📄"


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
