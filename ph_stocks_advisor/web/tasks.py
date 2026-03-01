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

# Redis key prefix — must match the one in app.py.
_INFLIGHT_PREFIX = "analysis:inflight:"


def _clear_inflight_lock(symbol: str) -> None:
    """Remove the inflight dedup lock for *symbol* from Redis."""
    import redis as redis_lib

    from ph_stocks_advisor.infra.config import get_settings

    try:
        r = redis_lib.from_url(get_settings().redis_url, decode_responses=True)
        r.delete(f"{_INFLIGHT_PREFIX}{symbol}")
    except Exception:
        logger.debug("Could not clear inflight lock for %s", symbol, exc_info=True)


@celery_app.task(bind=True, name="analyse_stock")
def analyse_stock(self, symbol: str, user_id: str = "anonymous") -> dict:
    """Run the full multi-agent analysis for a stock symbol.

    Returns a dict with ``symbol``, ``verdict``, and ``report_id``
    so the web app can retrieve / display the result.
    """
    from ph_stocks_advisor.data.models import FinalReport
    from ph_stocks_advisor.graph.workflow import run_analysis
    from ph_stocks_advisor.infra.config import get_repository
    from ph_stocks_advisor.infra.repository import ReportRecord
    from ph_stocks_advisor.web.progress import (
        STEP_FETCHING,
        STEP_SAVING,
        STEP_VALIDATING,
        publish_progress,
    )
    from ph_stocks_advisor.web.rate_limit import increment as rl_increment

    task_id = self.request.id
    logger.info("Starting analysis for %s (task %s)", symbol, task_id)

    # Notify the SSE stream that the analysis has begun.
    publish_progress(task_id, STEP_FETCHING)

    try:
        result = run_analysis(symbol, task_id=task_id)
        report: FinalReport | None = result.get("final_report")

        if report is None:
            error_msg = result.get("error", "Analysis produced no report.")
            logger.error("Analysis for %s failed: %s", symbol, error_msg)
            # Use STEP_VALIDATING if the error came from symbol validation,
            # otherwise use STEP_SAVING as a generic failure step.
            error_step = STEP_VALIDATING if result.get("error") else STEP_SAVING
            publish_progress(task_id, error_step, done=True, error=error_msg)
            return {"symbol": symbol, "error": error_msg}

        # Persist to database
        publish_progress(task_id, STEP_SAVING)
        repo = get_repository()
        try:
            record = ReportRecord.from_final_report(report)
            report_id = repo.save(record)
        finally:
            repo.close()

        # Count this successful analysis against the user's daily quota.
        try:
            import redis as redis_lib
            from ph_stocks_advisor.infra.config import get_settings

            rl_redis = redis_lib.from_url(
                get_settings().redis_url, decode_responses=True
            )
            rl_increment(rl_redis, user_id)
        except Exception:
            logger.warning(
                "Failed to increment rate-limit counter for %s",
                user_id,
                exc_info=True,
            )

        logger.info(
            "Analysis for %s complete — verdict=%s, report_id=%d",
            symbol,
            report.verdict.value,
            report_id,
        )
        publish_progress(
            task_id,
            STEP_SAVING,
            done=True,
            symbol=symbol,
            verdict=report.verdict.value,
            report_id=report_id,
        )
        return {
            "symbol": symbol,
            "verdict": report.verdict.value,
            "report_id": report_id,
        }
    except Exception as exc:
        publish_progress(task_id, STEP_SAVING, done=True, error=str(exc))
        raise
    finally:
        # Always clear the inflight dedup lock so a new analysis can run
        _clear_inflight_lock(symbol)
