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

# Redis key prefixes — must match the ones in app.py.
_INFLIGHT_PREFIX = "analysis:inflight:"
_INFLIGHT_TASK_PREFIX = "analysis:task:"
_PORTFOLIO_INFLIGHT_PREFIX = "portfolio:inflight:"


def _clear_inflight_lock(symbol: str, task_id: str | None = None) -> None:
    """Remove the inflight dedup lock and reverse mapping for *symbol*."""
    from ph_stocks_advisor.infra.config import get_redis

    try:
        r = get_redis()
        keys_to_delete = [f"{_INFLIGHT_PREFIX}{symbol}"]
        if task_id:
            keys_to_delete.append(f"{_INFLIGHT_TASK_PREFIX}{task_id}")
        r.delete(*keys_to_delete)
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
    from ph_stocks_advisor.web.rate_limit import release as rl_release

    task_id = self.request.id
    logger.info("Starting analysis for %s (task %s)", symbol, task_id)

    # Notify the SSE stream that the analysis has begun.
    publish_progress(task_id, STEP_FETCHING)

    try:
        result = run_analysis(symbol, task_id=task_id, user_id=user_id)
        report: FinalReport | None = result.get("final_report")

        if report is None:
            error_msg = result.get("error", "Analysis produced no report.")
            logger.error("Analysis for %s failed: %s", symbol, error_msg)
            # Release the reserved rate-limit slot so the user can retry.
            try:
                from ph_stocks_advisor.infra.config import get_redis

                rl_redis = get_redis()
                rl_release(rl_redis, user_id)
            except Exception:
                logger.warning(
                    "Failed to release rate-limit slot for %s",
                    user_id,
                    exc_info=True,
                )
            # Use STEP_VALIDATING if the error came from symbol validation,
            # otherwise use STEP_SAVING as a generic failure step.
            error_step = STEP_VALIDATING if result.get("error") else STEP_SAVING
            publish_progress(task_id, error_step, done=True, error=error_msg)
            return {"symbol": symbol, "error": error_msg}

        # Persist to database
        publish_progress(task_id, STEP_SAVING)
        repo = get_repository()
        record = ReportRecord.from_final_report(report)
        report_id = repo.save(record)

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
        _clear_inflight_lock(symbol, task_id=task_id)


@celery_app.task(bind=True, name="portfolio_analyse_stock")
def portfolio_analyse_stock(
    self,
    preceding_result: dict | None = None,
    *,
    symbol: str | None = None,
    user_id: str,
    shares: float,
    avg_cost: float,
    base_report_id: int | None = None,
) -> dict:
    """Run a portfolio-aware analysis for an elevated user's holding.

    When chained after ``analyse_stock``, *preceding_result* carries the
    base analysis output (with ``report_id`` and ``symbol``).  When
    called standalone, ``symbol`` and ``base_report_id`` must be
    provided explicitly.
    """
    from ph_stocks_advisor.agents.portfolio import PortfolioAgent
    from ph_stocks_advisor.infra.config import get_llm, get_repository
    from ph_stocks_advisor.infra.repository import PortfolioReportRecord

    # Resolve symbol / base_report_id from the preceding chain result
    # if they weren't given explicitly.
    if preceding_result and isinstance(preceding_result, dict):
        if preceding_result.get("error"):
            return {
                "symbol": preceding_result.get("symbol", symbol),
                "error": f"Base analysis failed: {preceding_result['error']}",
            }
        symbol = symbol or preceding_result.get("symbol")
        base_report_id = base_report_id or preceding_result.get("report_id")

    if not symbol:
        return {"error": "Symbol is required."}
    if not base_report_id:
        return {"symbol": symbol, "error": "Base report ID is required."}

    task_id = self.request.id
    logger.info("Portfolio analysis for %s (user=%s, task=%s)", symbol, user_id, task_id)

    try:
        repo = get_repository()
        record = repo.get_by_id(base_report_id)
        if record is None:
            return {"symbol": symbol, "error": "Base report not found."}

        # Fetch current price for P/L calculation.
        current_price = 0.0
        try:
            from ph_stocks_advisor.data.services.price import fetch_stock_price

            price_data = fetch_stock_price(symbol)
            if price_data and price_data.current_price > 0:
                current_price = price_data.current_price
        except Exception:
            logger.debug("Could not fetch live price for %s", symbol)

        if current_price <= 0:
            # Fallback: try to parse from the report
            current_price = avg_cost  # conservative fallback

        llm = get_llm()
        agent = PortfolioAgent(llm)
        analysis = agent.run(
            symbol=symbol,
            shares=shares,
            avg_cost=avg_cost,
            current_price=current_price,
            base_report=record.summary or "",
            sentiment_context=getattr(record, "sentiment_section", "") or "",
            user_id=user_id,
            session_id=task_id,
        )

        # Persist the portfolio report.
        pr = PortfolioReportRecord(
            id=None,
            user_id=user_id,
            symbol=symbol,
            shares=shares,
            avg_cost=avg_cost,
            analysis=analysis,
            base_report_id=base_report_id,
        )
        report_id = repo.save_portfolio_report(pr)
        logger.info(
            "Portfolio analysis for %s complete — report_id=%d",
            symbol,
            report_id,
        )
        return {
            "symbol": symbol,
            "report_id": report_id,
            "status": "done",
        }
    except Exception as exc:
        logger.error("Portfolio analysis failed for %s: %s", symbol, exc)
        return {"symbol": symbol, "error": str(exc)}
    finally:
        # Clear the portfolio in-flight lock so the page shows the
        # result instead of the spinner on the next load.
        try:
            from ph_stocks_advisor.infra.config import get_redis

            r = get_redis()
            pf_key = f"{_PORTFOLIO_INFLIGHT_PREFIX}{user_id}:{symbol}"
            r.delete(pf_key)
        except Exception:
            logger.debug("Could not clear portfolio inflight lock for %s", symbol)
