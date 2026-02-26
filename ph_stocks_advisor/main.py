"""
CLI entry point for the PH Stocks Advisor.

Usage:
    ph-advisor TEL
    ph-advisor SM BDO TEL            # analyse multiple stocks
    ph-advisor SM --pdf               # also generate PDF
    ph-advisor SM --html              # also generate HTML
    ph-advisor SM --pdf -o out.pdf    # custom output path (single symbol only)
    ph-advisor SM --html -o out.html

Export only (no new analysis):
    ph-advisor-pdf MREIT          # latest report → PDF
    ph-advisor-html MREIT         # latest report → HTML
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ph_stocks_advisor.infra.config import get_repository
from ph_stocks_advisor.graph.workflow import run_analysis
from ph_stocks_advisor.data.models import FinalReport
from ph_stocks_advisor.infra.repository import ReportRecord
from ph_stocks_advisor.export import FORMATTER_REGISTRY, get_formatter




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
    parser.add_argument(
        "symbols", nargs="+",
        help="One or more PSE stock symbols (e.g. TEL SM MREIT)",
    )

    # Dynamically add --pdf, --html, … from the formatter registry
    for name in FORMATTER_REGISTRY:
        parser.add_argument(
            f"--{name}", action="store_true",
            help=f"Generate a {name.upper()} report after analysis",
        )

    parser.add_argument(
        "-o", "--output", type=str, default=None,
        help="Output path (default: <SYMBOL>_report.<ext>)",
    )
    return parser.parse_args()


def _analyse_single(symbol: str, requested_formats: list[str],
                     output_path: str | None) -> bool:
    """Run analysis for one symbol. Returns True on success."""
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
        return False

    # Check if the symbol validation failed
    error = result.get("error")
    if error:
        print(f"❌ {error}")
        return False

    report = result.get("final_report")

    if report is None:
        print("❌ Analysis failed — no report was generated.")
        return False

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

    # Export to any requested output formats
    if requested_formats:
        rec = ReportRecord.from_final_report(report)
        for fmt_name in requested_formats:
            formatter = get_formatter(fmt_name)
            out = Path(output_path or f"{symbol}_report{formatter.file_extension}")
            formatter.write(rec, out)
            print(f"{formatter.emoji} {formatter.format_label} saved to {out}")

    return True


def main(symbol: str | None = None) -> None:
    """Run the multi-agent analysis, save to DB, and print the report."""

    # Parse CLI args
    requested_formats: list[str] = []
    output_path: str | None = None
    symbols: list[str] = []

    if symbol is None:
        args = _parse_args()
        symbols = args.symbols
        output_path = args.output
        for name in FORMATTER_REGISTRY:
            if getattr(args, name, False):
                requested_formats.append(name)
    else:
        symbols = [symbol]
        # Called programmatically — check leftover argv for flags
        for name in FORMATTER_REGISTRY:
            if f"--{name}" in sys.argv:
                requested_formats.append(name)

    if len(symbols) > 1 and output_path:
        print("⚠️  -o/--output ignored when analysing multiple symbols "
              "(each file is auto-named <SYMBOL>_report.<ext>).")
        output_path = None

    failures: list[str] = []
    for sym in symbols:
        ok = _analyse_single(sym, requested_formats, output_path)
        if not ok:
            failures.append(sym)

    if len(symbols) > 1:
        print(f"\n{'=' * 60}")
        print(f"  Completed {len(symbols) - len(failures)}/{len(symbols)} analyses.")
        if failures:
            print(f"  Failed: {', '.join(failures)}")
        print(f"{'=' * 60}\n")

    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
