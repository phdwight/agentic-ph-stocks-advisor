"""
Celery task definitions.

Each task encapsulates one unit of background work. The web app
dispatches tasks via ``.delay()``; the Celery worker executes them
in a separate container.

Single Responsibility: tasks only bridge the queue boundary — actual
analysis logic stays in ``graph.workflow``.
"""

from __future__ import annotations

import logging

from ph_stocks_advisor.web.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name="analyse_stock")
def analyse_stock(self, symbol: str) -> dict:
    """Run the full multi-agent analysis for a stock symbol.

    Returns a dict with ``symbol``, ``verdict``, and ``report_id``
    so the web app can retrieve / display the result.
    """
    from ph_stocks_advisor.data.models import FinalReport
    from ph_stocks_advisor.graph.workflow import run_analysis
    from ph_stocks_advisor.infra.config import get_repository
    from ph_stocks_advisor.infra.repository import ReportRecord

    logger.info("Starting analysis for %s (task %s)", symbol, self.request.id)

    result = run_analysis(symbol)
    report: FinalReport | None = result.get("final_report")

    if report is None:
        error_msg = result.get("error", "Analysis produced no report.")
        logger.error("Analysis for %s failed: %s", symbol, error_msg)
        return {"symbol": symbol, "error": error_msg}

    # Persist to database
    repo = get_repository()
    try:
        record = ReportRecord.from_final_report(report)
        report_id = repo.save(record)
    finally:
        repo.close()

    logger.info(
        "Analysis for %s complete — verdict=%s, report_id=%d",
        symbol,
        report.verdict.value,
        report_id,
    )
    return {
        "symbol": symbol,
        "verdict": report.verdict.value,
        "report_id": report_id,
    }
