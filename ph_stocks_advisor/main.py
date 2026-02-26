"""
CLI entry point for the PH Stocks Advisor.

Usage:
    python -m ph_stocks_advisor.main TEL
    python -m ph_stocks_advisor.main SM
"""

from __future__ import annotations

import logging
import sys

from ph_stocks_advisor.infra.config import get_repository
from ph_stocks_advisor.graph.workflow import run_analysis
from ph_stocks_advisor.data.models import FinalReport
from ph_stocks_advisor.infra.repository import ReportRecord

# Suppress noisy yfinance 404 warnings for tickers that only exist on PSE
logging.getLogger("yfinance").setLevel(logging.CRITICAL)


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


def main(symbol: str | None = None) -> None:
    """Run the multi-agent analysis, save to DB, and print the report."""
    if symbol is None:
        if len(sys.argv) < 2:
            print("Usage: python -m ph_stocks_advisor.main <SYMBOL>")
            print("Example: python -m ph_stocks_advisor.main TEL")
            sys.exit(1)
        symbol = sys.argv[1]

    symbol = symbol.upper().replace(".PS", "")
    print(f"\n🔍 Analysing {symbol} — this may take a minute …\n")

    result = run_analysis(symbol)

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
    repo = get_repository()
    try:
        record = ReportRecord.from_final_report(report)
        record_id = repo.save(record)
        print(f"💾 Report saved to database (id={record_id})")
    finally:
        repo.close()

    _print_report(report)


if __name__ == "__main__":
    main()
