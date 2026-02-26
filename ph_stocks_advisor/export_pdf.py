"""
Export a saved stock-analysis report as a styled PDF.

Usage (standalone):
    python -m ph_stocks_advisor.export_pdf MREIT          # latest report
    python -m ph_stocks_advisor.export_pdf MREIT --id 26  # specific report id
    python -m ph_stocks_advisor.export_pdf MREIT -o ~/Desktop/MREIT.pdf
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from fpdf import FPDF

from ph_stocks_advisor.infra.config import get_repository
from ph_stocks_advisor.infra.repository import ReportRecord


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_FONT_DIR = None  # uses built-in fonts (Helvetica)
_PAGE_W = 210  # A4 mm
_MARGIN = 15
_BODY_W = _PAGE_W - 2 * _MARGIN


# ---------------------------------------------------------------------------
# PDF builder
# ---------------------------------------------------------------------------

class _ReportPDF(FPDF):
    """Custom FPDF subclass with header/footer branding."""

    def __init__(self, symbol: str, verdict: str) -> None:
        super().__init__(orientation="P", unit="mm", format="A4")
        self._symbol = symbol
        self._verdict = verdict
        self.set_auto_page_break(auto=True, margin=20)
        self.set_margins(_MARGIN, _MARGIN, _MARGIN)

    # -- header on every page --
    def header(self) -> None:
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(120, 120, 120)
        self.cell(0, 6, "Philippine Stock Advisor", align="L")
        self.cell(0, 6, self._symbol, align="R", new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(200, 200, 200)
        self.line(_MARGIN, self.get_y(), _PAGE_W - _MARGIN, self.get_y())
        self.ln(4)

    # -- footer on every page --
    def footer(self) -> None:
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(160, 160, 160)
        self.cell(0, 10, f"Page {self.page_no()}/{{nb}}", align="C")


# Characters outside latin-1 that appear in PH stock reports
_UNICODE_SUBS: dict[str, str] = {
    "\u20b1": "PHP ",   # ₱  → PHP
    "\u2022": "-",      # •  → -
    "\u2013": "-",      # –  → -
    "\u2014": "-",      # —  → -
    "\u2018": "'",      # '  → '
    "\u2019": "'",      # '  → '
    "\u201c": '"',      # "  → "
    "\u201d": '"',      # "  → "
    "\u2026": "...",    # …  → ...
}


def _sanitize(text: str) -> str:
    """Replace non-latin-1 characters so built-in PDF fonts can render them."""
    for char, repl in _UNICODE_SUBS.items():
        text = text.replace(char, repl)
    # Drop any remaining non-latin-1 chars
    return text.encode("latin-1", errors="replace").decode("latin-1")


def _strip_markdown_bold(text: str) -> str:
    """Remove **bold** markers from text."""
    return re.sub(r"\*\*(.+?)\*\*", r"\1", text)


def _write_section(pdf: _ReportPDF, title: str, body: str) -> None:
    """Write a titled section, handling bullet lists and paragraphs."""
    # Section heading
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(30, 60, 120)
    pdf.cell(0, 8, _sanitize(_strip_markdown_bold(title)), new_x="LMARGIN", new_y="NEXT")
    pdf.set_draw_color(30, 60, 120)
    pdf.line(_MARGIN, pdf.get_y(), _MARGIN + 60, pdf.get_y())
    pdf.ln(3)

    # Body text
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(40, 40, 40)

    for line in body.strip().splitlines():
        line = line.strip()
        if not line:
            pdf.ln(2)
            continue

        clean = _sanitize(_strip_markdown_bold(line))

        # Bullet point
        if clean.startswith("- ") or clean.startswith("* "):
            bullet_text = clean[2:].strip()
            pdf.cell(6, 5, "-")
            pdf.multi_cell(_BODY_W - 8, 5, f" {bullet_text}")
            pdf.ln(1)
        else:
            pdf.multi_cell(_BODY_W, 5, clean)
            pdf.ln(1)

    pdf.ln(3)


def build_pdf(record: ReportRecord) -> bytes:
    """Build a PDF from a ReportRecord and return the raw bytes."""

    pdf = _ReportPDF(symbol=record.symbol, verdict=record.verdict)
    pdf.alias_nb_pages()
    pdf.add_page()

    # ── Title block ──
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(20, 40, 80)
    pdf.cell(0, 12, f"{record.symbol} Stock Analysis", new_x="LMARGIN", new_y="NEXT")

    # Verdict badge
    is_buy = record.verdict.upper() == "BUY"
    pdf.set_font("Helvetica", "B", 14)
    if is_buy:
        pdf.set_fill_color(34, 139, 34)
    else:
        pdf.set_fill_color(200, 50, 50)
    pdf.set_text_color(255, 255, 255)
    badge = f"  Verdict: {record.verdict}  "
    pdf.cell(pdf.get_string_width(badge) + 6, 10, badge, fill=True,
             new_x="LMARGIN", new_y="NEXT")

    # Date
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(130, 130, 130)
    ts = record.created_at.strftime("%B %d, %Y %I:%M %p") if record.created_at else ""
    pdf.cell(0, 8, f"Generated: {ts}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    # ── Executive Summary ──
    # The summary field contains the full consolidated report.
    # Parse out sections from it, or write the whole thing.
    _write_full_summary(pdf, record)

    return pdf.output()


def _write_full_summary(pdf: _ReportPDF, record: ReportRecord) -> None:
    """Parse the consolidated summary into sections and render each."""
    summary = record.summary or ""

    # Try to split by markdown-style headings (** heading **)
    # Pattern: lines starting with ** ... ** or --- separators
    sections: list[tuple[str, str]] = []
    current_title = "Executive Summary"
    current_lines: list[str] = []

    for line in summary.splitlines():
        stripped = line.strip()
        # Skip --- dividers
        if stripped == "---":
            continue
        # Check for a heading pattern like **Price Analysis:** or **Verdict:**
        heading_match = re.match(r"^\*\*(.+?):\*\*\s*$", stripped)
        if heading_match:
            # Save previous section
            if current_lines:
                sections.append((current_title, "\n".join(current_lines)))
                current_lines = []
            current_title = heading_match.group(1)
            continue

        # Also match heading with content on same line: **Title:** content...
        heading_inline = re.match(r"^\*\*(.+?):\*\*\s+(.+)$", stripped)
        if heading_inline:
            if current_lines:
                sections.append((current_title, "\n".join(current_lines)))
                current_lines = []
            current_title = heading_inline.group(1)
            current_lines.append(heading_inline.group(2))
            continue

        current_lines.append(line)

    # Last section
    if current_lines:
        sections.append((current_title, "\n".join(current_lines)))

    # Render each section
    for title, body in sections:
        body = body.strip()
        if not body:
            continue
        # Verdict section gets special styling
        if title.lower().startswith("verdict"):
            continue  # already shown in badge
        _write_section(pdf, title, body)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Export a saved stock report as PDF."
    )
    parser.add_argument("symbol", help="Stock symbol (e.g. MREIT)")
    parser.add_argument("--id", type=int, default=None,
                        help="Specific report ID (default: latest)")
    parser.add_argument("-o", "--output", type=str, default=None,
                        help="Output PDF path (default: <SYMBOL>_report.pdf)")
    args = parser.parse_args()

    symbol = args.symbol.upper().replace(".PS", "")

    repo = get_repository()
    try:
        if args.id:
            record = repo.get_by_id(args.id)
            if record and record.symbol != symbol:
                print(f"⚠️  Report id={args.id} is for {record.symbol}, not {symbol}")
        else:
            record = repo.get_latest_by_symbol(symbol)
    finally:
        repo.close()

    if record is None:
        print(f"❌ No report found for {symbol}.")
        sys.exit(1)

    print(f"📄 Exporting report id={record.id} for {record.symbol} "
          f"(verdict: {record.verdict}, date: {record.created_at})…")

    pdf_bytes = build_pdf(record)

    out_path = args.output or f"{symbol}_report.pdf"
    Path(out_path).write_bytes(pdf_bytes)
    print(f"✅ PDF saved to {out_path}")


if __name__ == "__main__":
    main()
