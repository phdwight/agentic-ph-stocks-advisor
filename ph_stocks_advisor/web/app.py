"""
Flask application factory and CLI entry point.

Single Responsibility: this module only handles HTTP routing and
request/response logic. Analysis is dispatched to a Celery worker
via the ``analyse_stock`` task; persistence uses the ``infra.repository``
abstraction.

Dependency Inversion: the web layer depends on the task queue
abstraction (Celery) rather than calling ``run_analysis`` directly,
enabling the worker to live in a separate container.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from flask import Flask, jsonify, render_template, request

from ph_stocks_advisor.export.formatter import (
    DATA_SOURCES,
    DISCLAIMER,
    format_timestamp,
    parse_sections,
)
from ph_stocks_advisor.infra.config import get_repository

logger = logging.getLogger(__name__)

# Reports older than this are considered stale and re-analysed.
REPORT_MAX_AGE_DAYS = 5


def create_app() -> Flask:
    """Application factory — returns a configured Flask instance."""
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )
    app.config["SECRET_KEY"] = "ph-stocks-advisor-dev-key"

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------

    @app.route("/")
    def index():
        """Landing page with the analysis form and previously analysed stocks."""
        repo = get_repository()
        try:
            recent = repo.list_recent_symbols(limit=50)
        except Exception:
            recent = []
        return render_template("index.html", recent_stocks=recent)

    @app.route("/analyse", methods=["POST"])
    def analyse():
        """Check for a fresh cached report; dispatch to Celery if stale/missing."""
        from ph_stocks_advisor.web.tasks import analyse_stock

        symbol = (request.form.get("symbol") or "").strip().upper().replace(".PS", "")
        if not symbol:
            return jsonify({"error": "Symbol is required"}), 400

        # Check for a recent report (within REPORT_MAX_AGE_DAYS)
        repo = get_repository()
        try:
            record = repo.get_latest_by_symbol(symbol)
        finally:
            repo.close()

        if record and record.created_at:
            age = datetime.now(tz=UTC) - record.created_at
            if age <= timedelta(days=REPORT_MAX_AGE_DAYS):
                logger.info(
                    "Fresh report found for %s (age=%s), serving cached.",
                    symbol,
                    age,
                )
                return jsonify({
                    "status": "cached",
                    "symbol": symbol,
                    "report_id": record.id,
                })

        # No fresh report — dispatch analysis to the Celery worker
        task = analyse_stock.delay(symbol)
        return jsonify({"status": "started", "symbol": symbol, "task_id": task.id})

    @app.route("/status/<task_id>")
    def status(task_id: str):
        """Poll the status of a Celery task."""
        from ph_stocks_advisor.web.tasks import analyse_stock

        result = analyse_stock.AsyncResult(task_id)

        if result.state == "PENDING":
            return jsonify({"state": "PENDING", "done": False})

        if result.state == "STARTED":
            return jsonify({"state": "STARTED", "done": False})

        if result.state == "SUCCESS":
            data = result.result or {}
            return jsonify({
                "state": "SUCCESS",
                "done": True,
                "symbol": data.get("symbol", ""),
                "verdict": data.get("verdict", ""),
                "report_id": data.get("report_id"),
                "error": data.get("error"),
            })

        if result.state == "FAILURE":
            return jsonify({
                "state": "FAILURE",
                "done": True,
                "error": str(result.info),
            })

        if result.state == "REVOKED":
            return jsonify({
                "state": "REVOKED",
                "done": True,
                "error": "Analysis was cancelled.",
            })

        # RETRY, etc.
        return jsonify({"state": result.state, "done": False})

    @app.route("/cancel/<task_id>", methods=["POST"])
    def cancel(task_id: str):
        """Revoke (cancel) a running Celery task."""
        from ph_stocks_advisor.web.tasks import celery_app

        celery_app.control.revoke(task_id, terminate=True, signal="SIGTERM")
        return jsonify({"status": "cancelled", "task_id": task_id})

    @app.route("/report/<symbol>")
    def report(symbol: str):
        """Display the latest report for a symbol."""
        symbol = symbol.upper().replace(".PS", "")
        repo = get_repository()
        try:
            record = repo.get_latest_by_symbol(symbol)
        finally:
            repo.close()

        if record is None:
            return render_template("no_report.html", symbol=symbol), 404

        sections = parse_sections(record.summary or "")
        is_buy = record.verdict.upper() == "BUY"
        ts = format_timestamp(record.created_at)

        # Determine if the report is a cached result
        is_cached = False
        if record.created_at:
            age = datetime.now(tz=UTC) - record.created_at
            is_cached = age <= timedelta(days=REPORT_MAX_AGE_DAYS)

        return render_template(
            "report.html",
            record=record,
            sections=sections,
            is_buy=is_buy,
            is_cached=is_cached,
            timestamp=ts,
            data_sources=DATA_SOURCES,
            disclaimer=DISCLAIMER,
        )

    @app.route("/history/<symbol>")
    def history(symbol: str):
        """List all saved reports for a symbol."""
        symbol = symbol.upper().replace(".PS", "")
        repo = get_repository()
        try:
            records = repo.list_by_symbol(symbol, limit=20)
        finally:
            repo.close()

        formatted = []
        for r in records:
            formatted.append(
                {
                    "id": r.id,
                    "symbol": r.symbol,
                    "verdict": r.verdict,
                    "created_at": format_timestamp(r.created_at),
                }
            )

        return render_template("history.html", symbol=symbol, reports=formatted)

    @app.route("/report-by-id/<int:report_id>")
    def report_by_id(report_id: int):
        """Display a specific report by its database ID."""
        repo = get_repository()
        try:
            record = repo.get_by_id(report_id)
        finally:
            repo.close()

        if record is None:
            return render_template("no_report.html", symbol="unknown"), 404

        sections = parse_sections(record.summary or "")
        is_buy = record.verdict.upper() == "BUY"
        ts = format_timestamp(record.created_at)

        return render_template(
            "report.html",
            record=record,
            sections=sections,
            is_buy=is_buy,
            timestamp=ts,
            data_sources=DATA_SOURCES,
            disclaimer=DISCLAIMER,
        )

    return app


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the Flask development server."""
    import argparse

    parser = argparse.ArgumentParser(description="PH Stocks Advisor Web UI")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    parser.add_argument("--port", type=int, default=5000, help="Port to bind to")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    args = parser.parse_args()

    app = create_app()
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
