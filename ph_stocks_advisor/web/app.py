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

import json

import redis as redis_lib
from flask import Flask, Response, jsonify, render_template, request
from werkzeug.middleware.proxy_fix import ProxyFix

from markupsafe import Markup

from ph_stocks_advisor.export.formatter import (
    DATA_SOURCES,
    DISCLAIMER,
    format_timestamp,
    parse_sections,
)
from ph_stocks_advisor.export.html import _body_to_html
from ph_stocks_advisor.infra.config import get_repository, get_settings
from ph_stocks_advisor.web.auth import auth_bp, get_current_user, login_required
from ph_stocks_advisor.web.rate_limit import check_and_increment

logger = logging.getLogger(__name__)

# Reports older than this are considered stale and re-analysed.
REPORT_MAX_AGE_DAYS = 5

# Redis key prefix for in-flight analysis dedup locks.
_INFLIGHT_PREFIX = "analysis:inflight:"
# How long the lock lives before auto-expiring (seconds).
_INFLIGHT_TTL = 10 * 60  # 10 minutes


def _get_redis() -> redis_lib.Redis:
    """Return a Redis client using the configured URL."""
    return redis_lib.from_url(get_settings().redis_url, decode_responses=True)


def create_app() -> Flask:
    """Application factory — returns a configured Flask instance."""
    settings = get_settings()
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )
    app.config["SECRET_KEY"] = settings.flask_secret_key
    app.config["SESSION_COOKIE_SECURE"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

    # Try server-side Redis sessions.  If Redis is unreachable fall back
    # to the default signed-cookie sessions (safe now that we no longer
    # store the large MSAL token cache in the session).
    try:
        r = redis_lib.from_url(settings.redis_url, socket_connect_timeout=3)
        r.ping()
        app.config["SESSION_TYPE"] = "redis"
        app.config["SESSION_PERMANENT"] = False
        app.config["SESSION_REDIS"] = r
        from flask_session import Session
        Session(app)
        logger.info("Server-side Redis sessions enabled.")
    except Exception:
        logger.warning(
            "Redis unavailable for sessions — using signed-cookie sessions."
        )

    # Trust reverse-proxy headers (Azure Container Apps, nginx, etc.)
    # so that request.url_root uses https:// when behind TLS termination.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)  # type: ignore[assignment]

    # Register the Entra ID authentication blueprint.
    app.register_blueprint(auth_bp)

    @app.template_filter("md_to_html")
    def md_to_html_filter(text: str) -> Markup:
        """Convert light-markdown section body to formatted HTML."""
        return Markup(_body_to_html(text))

    @app.context_processor
    def inject_user():
        """Make ``current_user`` available in every template."""
        return {"current_user": get_current_user()}

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------

    @app.route("/")
    @login_required
    def index():
        """Landing page with the analysis form and user's analysed stocks.

        Authenticated users see only the stocks they have previously
        requested.  Anonymous users (auth disabled) see all recent
        symbols.
        """
        repo = get_repository()
        try:
            user = get_current_user()
            if user and user.get("email"):
                recent = repo.list_user_symbols(
                    user_id=user["email"], limit=50
                )
            else:
                recent = repo.list_recent_symbols(limit=50)
        except Exception:
            recent = []
        finally:
            repo.close()
        return render_template("index.html", recent_stocks=recent)

    @app.route("/analyse", methods=["POST"])
    @login_required
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
                # Track symbol for the current user.
                user = get_current_user()
                if user and user.get("email"):
                    try:
                        repo2 = get_repository()
                        repo2.add_user_symbol(user["email"], symbol)
                        repo2.close()
                    except Exception:
                        logger.debug("Failed to record user-symbol link.")
                return jsonify({
                    "status": "cached",
                    "symbol": symbol,
                    "report_id": record.id,
                })

        # No fresh report — check for an in-flight analysis (dedup)
        r = _get_redis()
        inflight_key = f"{_INFLIGHT_PREFIX}{symbol}"
        existing_task_id = r.get(inflight_key)
        if existing_task_id:
            logger.info(
                "In-flight analysis found for %s (task %s), joining.",
                symbol,
                existing_task_id,
            )
            return jsonify({
                "status": "joined",
                "symbol": symbol,
                "task_id": existing_task_id,
            })

        # --- Per-user daily rate limit --------------------------------
        user = get_current_user()
        user_id = (user or {}).get("email", "anonymous")
        allowed, count = check_and_increment(
            r, user_id, settings.daily_analysis_limit
        )
        if not allowed:
            logger.warning(
                "User %s exceeded daily analysis limit (%d/%d).",
                user_id,
                count,
                settings.daily_analysis_limit,
            )
            next_midnight = (
                datetime.now(tz=UTC) + timedelta(days=1)
            ).replace(hour=0, minute=0, second=0, microsecond=0)
            return jsonify({
                "error": (
                    f"Daily analysis limit reached ({settings.daily_analysis_limit} per day). "
                    "Your quota resets at midnight UTC."
                ),
                "reset_at": next_midnight.isoformat(),
            }), 429

        # Dispatch analysis to the Celery worker
        task = analyse_stock.delay(symbol)

        # Store the lock so concurrent requests join this task
        r.set(inflight_key, task.id, ex=_INFLIGHT_TTL)

        # Track symbol for the current user.
        if user and user.get("email"):
            try:
                repo2 = get_repository()
                repo2.add_user_symbol(user["email"], symbol)
                repo2.close()
            except Exception:
                logger.debug("Failed to record user-symbol link.")

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

    @app.route("/stream/<task_id>")
    def stream(task_id: str):
        """SSE endpoint that pushes real-time progress events for a task.

        Uses Redis Pub/Sub so that the Celery worker can publish step
        updates and this endpoint relays them to the browser via
        ``text/event-stream``.

        The stream auto-closes after a terminal (``done=true``) event
        or when the client disconnects.  Clients that do not support
        SSE can fall back to ``/status/<task_id>`` polling.
        """
        from ph_stocks_advisor.web.progress import subscribe_progress

        def generate():
            for event in subscribe_progress(task_id):
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("done"):
                    break

        return Response(
            generate(),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.route("/cancel/<task_id>", methods=["POST"])
    def cancel(task_id: str):
        """Revoke (cancel) a running Celery task and clear inflight lock."""
        from ph_stocks_advisor.web.tasks import celery_app

        celery_app.control.revoke(task_id, terminate=True, signal="SIGTERM")

        # Clear the inflight lock so a new analysis can be dispatched
        r = _get_redis()
        for key in r.scan_iter(f"{_INFLIGHT_PREFIX}*"):
            if r.get(key) == task_id:
                r.delete(key)
                break

        return jsonify({"status": "cancelled", "task_id": task_id})

    @app.route("/report/<symbol>")
    @login_required
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
    @login_required
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
    @login_required
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
