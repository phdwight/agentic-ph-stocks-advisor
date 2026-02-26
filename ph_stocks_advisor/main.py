"""
CLI entry point for the PH Stocks Advisor.

Usage:
    python -m ph_stocks_advisor.main TEL
    python -m ph_stocks_advisor.main SM
    python -m ph_stocks_advisor.main SM --pdf             # also generate PDF
    python -m ph_stocks_advisor.main SM --html            # also generate HTML
    python -m ph_stocks_advisor.main SM --pdf -o out.pdf  # custom PDF output path
    python -m ph_stocks_advisor.main SM --html -o out.html

Export only (no new analysis):
    python -m ph_stocks_advisor.export_pdf MREIT          # latest report → PDF
    python -m ph_stocks_advisor.export_pdf MREIT --id 26  # specific report id
    python -m ph_stocks_advisor.export_html MREIT         # latest report → HTML
"""

from __future__ import annotations

import argparse
import logging
import sys

from ph_stocks_advisor.infra.config import get_repository
from ph_stocks_advisor.graph.workflow import run_analysis
from ph_stocks_advisor.data.models import FinalReport
from ph_stocks_advisor.infra.repository import ReportRecord




def _print_report(report: FinalReport) -> None:
    """Pretty-print the investment report to stdout."""
    border = "=" * 60
    print(f"\n{border}")
    print(f"  PHILIPPINE STOCK ADVISOR — {report.symbol}")
    print(border)
    print(f"\n{report.summary}")
    print(f"\n{border}")
    print(f"  VERDICT:  {report.verdict.value}")
    print(f"{border}\n")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Agentic AI Philippine Stock Advisor",
    )
    parser.add_argument("symbol", help="PSE stock symbol (e.g. TEL, SM, MREIT)")
    parser.add_argument(
        "--pdf", action="store_true",
        help="Generate a PDF report after analysis",
    )
    parser.add_argument(
        "--html", action="store_true",
        help="Generate an HTML report after analysis",
    )
    parser.add_argument(
        "-o", "--output", type=str, default=None,
        help="Output path for --pdf or --html (default: <SYMBOL>_report.<ext>)",
    )
    return parser.parse_args()


def main(symbol: str | None = None) -> None:
    """Run the multi-agent analysis, save to DB, and print the report."""

    # Parse CLI args
    generate_pdf = False
    generate_html = False
    output_path: str | None = None

    if symbol is None:
        args = _parse_args()
        symbol = args.symbol
        generate_pdf = args.pdf
        generate_html = args.html
        output_path = args.output
    else:
        # Called programmatically — check leftover argv for flags
        generate_pdf = "--pdf" in sys.argv
        generate_html = "--html" in sys.argv

    symbol = symbol.upper().replace(".PS", "")
    print(f"\n🔍 Analysing {symbol} — this may take a minute …\n")

    try:
        result = run_analysis(symbol)
    except KeyboardInterrupt:
        print("\n⚠️  Analysis interrupted by user.")
        sys.exit(130)
    except Exception as exc:
        print(f"❌ An unexpected error occurred while analysing {symbol}:")
        print(f"   {type(exc).__name__}: {exc}")
        print("\n   Please check your internet connection and API keys, then try again.")
        sys.exit(1)

    # Check if the symbol validation failed
    error = result.get("error")
    if error:
        print(f"❌ {error}")
        sys.exit(1)

    report = result.get("final_report")

    if report is None:
        print("❌ Analysis failed — no report was generated.")
        sys.exit(1)

    if isinstance(report, dict):
        report = FinalReport(**report)

    # Persist the report to the database
    try:
        repo = get_repository()
        try:
            record = ReportRecord.from_final_report(report)
            record_id = repo.save(record)
            print(f"💾 Report saved to database (id={record_id})")
        finally:
            repo.close()
    except Exception as exc:
        print(f"⚠️  Could not save report to database: {exc}")

    _print_report(report)

    # Optionally generate a PDF
    if generate_pdf:
        from pathlib import Path
        from ph_stocks_advisor.export_pdf import build_pdf

        rec = ReportRecord.from_final_report(report)
        pdf_bytes = build_pdf(rec)
        out = output_path or f"{symbol}_report.pdf"
        Path(out).write_bytes(pdf_bytes)
        print(f"📄 PDF saved to {out}")

    # Optionally generate an HTML report
    if generate_html:
        from pathlib import Path
        from ph_stocks_advisor.export_html import build_html

        rec = ReportRecord.from_final_report(report)
        html_str = build_html(rec)
        out = output_path or f"{symbol}_report.html"
        Path(out).write_text(html_str, encoding="utf-8")
        print(f"🌐 HTML saved to {out}")


if __name__ == "__main__":
    main()
