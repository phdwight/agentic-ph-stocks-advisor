"""
PDF output formatter.

Renders a :class:`~ph_stocks_advisor.infra.repository.ReportRecord` as a
styled A4 PDF document using *fpdf2*.
"""

from __future__ import annotations

import re
from pathlib import Path

from fpdf import FPDF

from ph_stocks_advisor.export.formatter import OutputFormatter, parse_sections
from ph_stocks_advisor.infra.repository import ReportRecord


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_PAGE_W = 210  # A4 mm
_MARGIN = 15
_BODY_W = _PAGE_W - 2 * _MARGIN


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

class _ReportPDF(FPDF):
    """Custom FPDF subclass with header/footer branding."""

    def __init__(self, symbol: str, verdict: str) -> None:
        super().__init__(orientation="P", unit="mm", format="A4")
        self._symbol = symbol
        self._verdict = verdict
        self.set_auto_page_break(auto=True, margin=20)
        self.set_margins(_MARGIN, _MARGIN, _MARGIN)

    def header(self) -> None:
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(120, 120, 120)
        self.cell(0, 6, "Philippine Stock Advisor", align="L")
        self.cell(0, 6, self._symbol, align="R", new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(200, 200, 200)
        self.line(_MARGIN, self.get_y(), _PAGE_W - _MARGIN, self.get_y())
        self.ln(4)

    def footer(self) -> None:
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(160, 160, 160)
        self.cell(0, 10, f"Page {self.page_no()}/{{nb}}", align="C")


# Characters outside latin-1 that appear in PH stock reports
_UNICODE_SUBS: dict[str, str] = {
    "\u20b1": "PHP ",   # ₱
    "\u2022": "-",      # •
    "\u2013": "-",      # –
    "\u2014": "-",      # —
    "\u2018": "'",      # '
    "\u2019": "'",      # '
    "\u201c": '"',      # "
    "\u201d": '"',      # "
    "\u2026": "...",    # …
}


def _sanitize(text: str) -> str:
    """Replace non-latin-1 characters so built-in PDF fonts can render them."""
    for char, repl in _UNICODE_SUBS.items():
        text = text.replace(char, repl)
    return text.encode("latin-1", errors="replace").decode("latin-1")


def _strip_markdown_bold(text: str) -> str:
    """Remove **bold** markers from text."""
    return re.sub(r"\*\*(.+?)\*\*", r"\1", text)


def _write_section(pdf: _ReportPDF, title: str, body: str) -> None:
    """Write a titled section, handling bullet lists and paragraphs."""
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(30, 60, 120)
    pdf.cell(0, 8, _sanitize(_strip_markdown_bold(title)), new_x="LMARGIN", new_y="NEXT")
    pdf.set_draw_color(30, 60, 120)
    pdf.line(_MARGIN, pdf.get_y(), _MARGIN + 60, pdf.get_y())
    pdf.ln(3)

    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(40, 40, 40)

    for line in body.strip().splitlines():
        line = line.strip()
        if not line:
            pdf.ln(2)
            continue

        # Strip leftover markdown artefacts before rendering
        clean = _sanitize(_strip_markdown_bold(line))
        # Remove trailing dashes the LLM sometimes appends
        clean = re.sub(r"-{2,}\s*$", "", clean)

        if clean.startswith("- ") or clean.startswith("* "):
            bullet_text = re.sub(r"^([-*]\s+)+", "", clean[2:]).strip()
            pdf.cell(6, 5, "-")
            pdf.multi_cell(_BODY_W - 8, 5, f" {bullet_text}")
            pdf.ln(1)
        else:
            pdf.multi_cell(_BODY_W, 5, clean)
            pdf.ln(1)

    pdf.ln(3)


# ---------------------------------------------------------------------------
# Formatter
# ---------------------------------------------------------------------------

class PdfFormatter(OutputFormatter):
    """Renders stock-analysis reports as styled A4 PDF documents."""

    @property
    def file_extension(self) -> str:
        return ".pdf"

    @property
    def format_label(self) -> str:
        return "PDF"

    @property
    def emoji(self) -> str:
        return "📄"

    def render(self, record: ReportRecord) -> bytes:  # noqa: D401
        """Build a PDF from *record* and return raw bytes."""
        pdf = _ReportPDF(symbol=record.symbol, verdict=record.verdict)
        pdf.alias_nb_pages()
        pdf.add_page()

        # Title block
        pdf.set_font("Helvetica", "B", 20)
        pdf.set_text_color(20, 40, 80)
        pdf.cell(0, 12, f"{record.symbol} Stock Analysis", new_x="LMARGIN", new_y="NEXT")

        # Verdict label + pill-shaped badge
        is_buy = record.verdict.upper() == "BUY"
        pdf.set_font("Helvetica", "B", 14)
        pdf.set_text_color(120, 120, 120)
        label = "Verdict: "
        pdf.cell(pdf.get_string_width(label), 10, label)

        # Pill badge (only the verdict value)
        if is_buy:
            pdf.set_fill_color(34, 139, 34)
        else:
            pdf.set_fill_color(200, 50, 50)
        pdf.set_text_color(255, 255, 255)
        badge_text = f" {record.verdict} "
        badge_w = pdf.get_string_width(badge_text) + 10
        badge_h = 10
        badge_x = pdf.get_x()
        badge_y = pdf.get_y()
        pdf.rect(badge_x, badge_y, badge_w, badge_h,
                 style="F", round_corners=True, corner_radius=badge_h / 2)
        pdf.set_xy(badge_x, badge_y)
        pdf.cell(badge_w, badge_h, badge_text, align="C",
                 new_x="LMARGIN", new_y="NEXT")

        # Date
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(130, 130, 130)
        ts = record.created_at.strftime("%B %d, %Y %I:%M %p") if record.created_at else ""
        pdf.cell(0, 8, f"Generated: {ts}", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(4)

        # Sections
        for title, body in parse_sections(record.summary or ""):
            body = body.strip()
            if not body or title.lower().startswith("verdict"):
                continue
            _write_section(pdf, title, body)

        return pdf.output()


# ---------------------------------------------------------------------------
# Standalone CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """``ph-advisor-pdf`` CLI — delegates to the shared :func:`export_cli`."""
    from ph_stocks_advisor.export.formatter import export_cli

    export_cli(PdfFormatter())


if __name__ == "__main__":
    main()
