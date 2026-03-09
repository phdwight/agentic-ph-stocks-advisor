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
from ph_stocks_advisor.infra.config import get_redis, get_repository, get_settings
from ph_stocks_advisor.web.auth import auth_bp, get_current_user, login_required
from ph_stocks_advisor.web.rate_limit import reserve as rl_reserve

logger = logging.getLogger(__name__)

# Reports older than this are considered stale and re-analysed.
REPORT_MAX_AGE_DAYS = 5

# Redis key prefix for in-flight analysis dedup locks.
_INFLIGHT_PREFIX = "analysis:inflight:"
# Reverse mapping: task_id -> symbol, for O(1) cancel lookup.
_INFLIGHT_TASK_PREFIX = "analysis:task:"
# How long the lock lives before auto-expiring (seconds).
_INFLIGHT_TTL = 10 * 60  # 10 minutes


def create_app() -> Flask:
    """Application factory — returns a configured Flask instance."""
    settings = get_settings()
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )
    app.config["SECRET_KEY"] = settings.flask_secret_key
    # Only mark the session cookie as Secure when running behind HTTPS
    # (i.e. when an identity provider is configured).  Local dev runs on
    # plain HTTP so a Secure cookie would be silently dropped by the
    # browser, preventing session persistence (e.g. elevated-mode toggle).
    app.config["SESSION_COOKIE_SECURE"] = settings.auth_enabled
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    # Cache static assets in browsers for 1 hour; reduces load at scale.
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 3600

    # Try server-side Redis sessions.  If Redis is unreachable fall back
    # to the default signed-cookie sessions (safe now that we no longer
    # store the large MSAL token cache in the session).
    try:
        from ph_stocks_advisor.infra.config import get_redis_raw
        session_redis = get_redis_raw()
        session_redis.ping()
        app.config["SESSION_TYPE"] = "redis"
        app.config["SESSION_PERMANENT"] = False
        app.config["SESSION_REDIS"] = session_redis
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
        """Make ``current_user`` and ``auth_enabled`` available in every template."""
        return {
            "current_user": get_current_user(),
            "auth_enabled": get_settings().auth_enabled,
        }

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    @app.route("/healthz")
    def healthz():
        """Heartbeat endpoint for liveness / readiness probes.

        Returns 200 with per-dependency status when the service is
        operational.  Returns 503 if any critical dependency (Redis,
        database) is unreachable — this lets orchestrators (Docker,
        Azure Container Apps) detect and restart unhealthy replicas.
        """
        checks: dict[str, str] = {}
        healthy = True

        # ── Redis ────────────────────────────────────────────────────
        try:
            r = get_redis()
            r.ping()
            checks["redis"] = "ok"
        except Exception as exc:
            checks["redis"] = f"error: {exc}"
            healthy = False

        # ── Database ─────────────────────────────────────────────────
        try:
            repo = get_repository()
            repo.list_recent_symbols(limit=1)

            checks["database"] = "ok"
        except Exception as exc:
            checks["database"] = f"error: {exc}"
            healthy = False

        status_code = 200 if healthy else 503
        return jsonify({"status": "healthy" if healthy else "unhealthy", "checks": checks}), status_code

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
        return render_template("index.html", recent_stocks=recent)

    @app.route("/analyse", methods=["POST"])
    @login_required
    def analyse():
        """Check for a fresh cached report; dispatch to Celery if stale/missing."""
        from ph_stocks_advisor.web.tasks import analyse_stock
        from ph_stocks_advisor.infra.repository import UserType

        symbol = (request.form.get("symbol") or "").strip().upper().replace(".PS", "")
        if not symbol:
            return jsonify({"error": "Symbol is required"}), 400

        # Determine if the current user has elevated privileges.
        user = get_current_user()
        is_elevated = (user or {}).get("user_type", 0) == UserType.ELEVATED

        # Check for a recent report (within REPORT_MAX_AGE_DAYS).
        # Elevated users bypass the multi-day cache but are still subject
        # to a per-stock daily cooldown (one analysis per UTC day).
        repo = get_repository()
        record = repo.get_latest_by_symbol(symbol)

        if record and record.created_at:
            now = datetime.now(tz=UTC)
            age = now - record.created_at

            if is_elevated:
                # Elevated cooldown: same UTC calendar day → blocked.
                report_date = record.created_at.date()
                today_utc = now.date()
                if report_date == today_utc:
                    next_midnight = (now + timedelta(days=1)).replace(
                        hour=0, minute=0, second=0, microsecond=0,
                    )
                    logger.info(
                        "Elevated cooldown: %s already analysed today, "
                        "next window %s.",
                        symbol,
                        next_midnight.isoformat(),
                    )
                    return jsonify({
                        "error": (
                            f"{symbol} was already analysed today. "
                            "You can re-analyse after midnight UTC."
                        ),
                        "reset_at": next_midnight.isoformat(),
                        "report_id": record.id,
                        "symbol": symbol,
                    }), 429
            else:
                # Normal users: serve the cached report if still fresh.
                if age <= timedelta(days=REPORT_MAX_AGE_DAYS):
                    logger.info(
                        "Fresh report found for %s (age=%s), serving cached.",
                        symbol,
                        age,
                    )
                    # Track symbol for the current user.
                    if user and user.get("email"):
                        try:
                            repo2 = get_repository()
                            repo2.add_user_symbol(user["email"], symbol)
                        except Exception:
                            logger.debug("Failed to record user-symbol link.")
                    return jsonify({
                        "status": "cached",
                        "symbol": symbol,
                        "report_id": record.id,
                    })

        # No fresh report — check for an in-flight analysis (dedup)
        r = get_redis()
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

        # --- Per-user daily rate limit (atomic reserve) ----------------
        # Elevated users are exempt from the daily analysis limit.
        user_id = (user or {}).get("email", "anonymous")
        if not is_elevated:
            allowed, count = rl_reserve(
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

        # Dispatch analysis to the Celery worker.
        # The slot is already reserved.  If the analysis fails the worker
        # calls ``release()`` to return the slot to the user's quota.
        task = analyse_stock.delay(symbol, user_id=user_id)

        # Store the lock so concurrent requests join this task
        r.set(inflight_key, task.id, ex=_INFLIGHT_TTL)
        # Reverse mapping for O(1) cancel lookup (avoids scan_iter)
        r.set(f"{_INFLIGHT_TASK_PREFIX}{task.id}", symbol, ex=_INFLIGHT_TTL)

        # Track symbol for the current user.
        if user and user.get("email"):
            try:
                repo2 = get_repository()
                repo2.add_user_symbol(user["email"], symbol)
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

        # Clear the inflight lock via O(1) reverse lookup (no keyspace scan)
        r = get_redis()
        reverse_key = f"{_INFLIGHT_TASK_PREFIX}{task_id}"
        symbol = r.get(reverse_key)
        if symbol:
            r.delete(f"{_INFLIGHT_PREFIX}{symbol}", reverse_key)

        return jsonify({"status": "cancelled", "task_id": task_id})

    @app.route("/report/<symbol>")
    @login_required
    def report(symbol: str):
        """Display the latest report for a symbol."""
        symbol = symbol.upper().replace(".PS", "")
        repo = get_repository()
        record = repo.get_latest_by_symbol(symbol)

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

        # Fetch live current price for the header display.
        current_price: float | None = None
        try:
            from ph_stocks_advisor.data.services.price import fetch_stock_price
            price_data = fetch_stock_price(symbol)
            if price_data and price_data.current_price > 0:
                current_price = price_data.current_price
        except Exception:
            logger.debug("Could not fetch live price for %s", symbol)

        # For elevated users: load their holding + portfolio report.
        from ph_stocks_advisor.infra.repository import UserType

        user = get_current_user()
        is_elevated = (user or {}).get("user_type", 0) == UserType.ELEVATED
        user_holding = None
        portfolio_report = None
        portfolio_on_cooldown = False
        if is_elevated and user and user.get("email"):
            try:
                user_holding = repo.get_holding(user["email"], symbol)
                portfolio_report = repo.get_portfolio_report(user["email"], symbol)
                # Check if portfolio analysis is on cooldown (already run today).
                if portfolio_report and portfolio_report.created_at:
                    now_utc = datetime.now(tz=UTC)
                    today_midnight_utc = now_utc.replace(
                        hour=0, minute=0, second=0, microsecond=0,
                    )
                    portfolio_on_cooldown = portfolio_report.created_at >= today_midnight_utc
            except Exception:
                logger.debug("Could not load holding/portfolio for %s", symbol)

        return render_template(
            "report.html",
            record=record,
            sections=sections,
            is_buy=is_buy,
            is_cached=is_cached,
            timestamp=ts,
            current_price=current_price,
            data_sources=DATA_SOURCES,
            disclaimer=DISCLAIMER,
            is_elevated=is_elevated,
            user_holding=user_holding,
            portfolio_report=portfolio_report,
            portfolio_on_cooldown=portfolio_on_cooldown,
        )

    @app.route("/history/<symbol>")
    @login_required
    def history(symbol: str):
        """List all saved reports for a symbol."""
        symbol = symbol.upper().replace(".PS", "")
        repo = get_repository()
        records = repo.list_by_symbol(symbol, limit=20)

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
        record = repo.get_by_id(report_id)

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

    # ------------------------------------------------------------------
    # Holdings (elevated users only)
    # ------------------------------------------------------------------

    @app.route("/api/holdings/<symbol>", methods=["GET"])
    @login_required
    def get_holding(symbol: str):
        """Return the current user's holding for a symbol."""
        from ph_stocks_advisor.infra.repository import UserType

        user = get_current_user()
        if not user or user.get("user_type", 0) != UserType.ELEVATED:
            return jsonify({"error": "Elevated access required"}), 403

        symbol = symbol.upper().replace(".PS", "")
        repo = get_repository()
        holding = repo.get_holding(user["email"], symbol)
        if holding is None:
            return jsonify({"holding": None})
        return jsonify({
            "holding": {
                "symbol": holding.symbol,
                "shares": holding.shares,
                "avg_cost": holding.avg_cost,
            }
        })

    @app.route("/api/holdings/<symbol>", methods=["POST"])
    @login_required
    def save_holding(symbol: str):
        """Save / update the current user's holding for a symbol."""
        from ph_stocks_advisor.infra.repository import HoldingRecord, UserType

        user = get_current_user()
        if not user or user.get("user_type", 0) != UserType.ELEVATED:
            return jsonify({"error": "Elevated access required"}), 403

        symbol = symbol.upper().replace(".PS", "")
        data = request.get_json(silent=True) or {}
        try:
            shares = float(data.get("shares", 0))
            avg_cost = float(data.get("avg_cost", 0))
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid shares or avg_cost"}), 400

        if shares <= 0 or avg_cost <= 0:
            return jsonify({"error": "Shares and avg_cost must be positive"}), 400

        repo = get_repository()
        holding = HoldingRecord(
            user_id=user["email"],
            symbol=symbol,
            shares=shares,
            avg_cost=avg_cost,
        )
        repo.save_holding(holding)
        return jsonify({"status": "saved", "symbol": symbol})

    @app.route("/api/holdings/<symbol>", methods=["DELETE"])
    @login_required
    def delete_holding(symbol: str):
        """Remove the current user's holding for a symbol."""
        from ph_stocks_advisor.infra.repository import UserType

        user = get_current_user()
        if not user or user.get("user_type", 0) != UserType.ELEVATED:
            return jsonify({"error": "Elevated access required"}), 403

        symbol = symbol.upper().replace(".PS", "")
        repo = get_repository()
        repo.delete_holding(user["email"], symbol)
        return jsonify({"status": "deleted", "symbol": symbol})

    # ------------------------------------------------------------------
    # Portfolio analysis (elevated users only)
    # ------------------------------------------------------------------

    @app.route("/api/portfolio-analyse/<symbol>", methods=["POST"])
    @login_required
    def portfolio_analyse(symbol: str):
        """Trigger a portfolio-aware analysis for the current user's holding."""
        from ph_stocks_advisor.web.tasks import portfolio_analyse_stock
        from ph_stocks_advisor.infra.repository import UserType

        user = get_current_user()
        if not user or user.get("user_type", 0) != UserType.ELEVATED:
            return jsonify({"error": "Elevated access required"}), 403

        symbol = symbol.upper().replace(".PS", "")
        repo = get_repository()

        # Require that the user has a holding saved for this symbol.
        holding = repo.get_holding(user["email"], symbol)
        if holding is None:
            return jsonify({"error": "No holding found for this symbol. Save your position first."}), 400

        # Require a base report to exist.
        record = repo.get_latest_by_symbol(symbol)
        if record is None:
            return jsonify({"error": "No analysis report found. Analyse the stock first."}), 400

        # Daily cooldown: one portfolio analysis per stock per day.
        # Resets at 8:00 AM GMT+8 (= 00:00 UTC).
        existing_pr = repo.get_portfolio_report(user["email"], symbol)
        if existing_pr and existing_pr.created_at:
            now_utc = datetime.now(tz=UTC)
            today_midnight_utc = now_utc.replace(
                hour=0, minute=0, second=0, microsecond=0,
            )
            if existing_pr.created_at >= today_midnight_utc:
                next_reset = today_midnight_utc + timedelta(days=1)
                return jsonify({
                    "error": (
                        f"Portfolio analysis for {symbol} was already run today. "
                        "You can re-analyse after 8:00 AM PHT tomorrow."
                    ),
                    "reset_at": next_reset.isoformat(),
                    "symbol": symbol,
                }), 429

        # Dispatch to Celery.
        task = portfolio_analyse_stock.delay(
            symbol,
            user_id=user["email"],
            shares=holding.shares,
            avg_cost=holding.avg_cost,
            base_report_id=record.id,
        )
        return jsonify({"status": "started", "task_id": task.id, "symbol": symbol})

    @app.route("/api/portfolio-report/<symbol>")
    @login_required
    def get_portfolio_report(symbol: str):
        """Return the latest portfolio report for the current user + symbol."""
        from ph_stocks_advisor.infra.repository import UserType

        user = get_current_user()
        if not user or user.get("user_type", 0) != UserType.ELEVATED:
            return jsonify({"error": "Elevated access required"}), 403

        symbol = symbol.upper().replace(".PS", "")
        repo = get_repository()
        pr = repo.get_portfolio_report(user["email"], symbol)
        if pr is None:
            return jsonify({"report": None})
        return jsonify({
            "report": {
                "id": pr.id,
                "symbol": pr.symbol,
                "shares": pr.shares,
                "avg_cost": pr.avg_cost,
                "analysis": pr.analysis,
                "analysis_html": _body_to_html(pr.analysis) if pr.analysis else "",
                "created_at": pr.created_at.isoformat() if pr.created_at else None,
            }
        })

    return app


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

# Default Gunicorn tuning — can be overridden via environment variables.
_DEFAULT_WORKERS = 4
_DEFAULT_THREADS = 2
_DEFAULT_WORKER_CLASS = "ph_stocks_advisor.web.worker.GeventWorkerNoSSL"


def main() -> None:
    """Start the web server.

    * **Production** (default): launches Gunicorn with sensible defaults.
      Tuning knobs via environment variables:

      - ``WEB_WORKERS``      — number of worker processes (default: 4)
      - ``WEB_THREADS``      — threads per worker, gthread only (default: 2)
      - ``WEB_WORKER_CLASS``  — Gunicorn worker class (default: gevent)
      - ``WEB_WORKER_CONNECTIONS`` — max simultaneous clients per worker,
        gevent only (default: 1000)
      - ``WEB_TIMEOUT``      — worker timeout in seconds (default: 120)

    * **Development** (``--debug``): falls back to Flask's built-in
      Werkzeug server with auto-reload.
    """
    import argparse
    import os

    parser = argparse.ArgumentParser(description="PH Stocks Advisor Web UI")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    parser.add_argument("--port", type=int, default=5000, help="Port to bind to")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    args = parser.parse_args()

    if args.debug:
        # Development: use Flask's built-in server with auto-reload.
        app = create_app()
        app.run(host=args.host, port=args.port, debug=True)
    else:
        # Production: launch Gunicorn.
        from gunicorn.app.wsgiapp import WSGIApplication  # noqa: WPS433

        workers = os.getenv("WEB_WORKERS", str(_DEFAULT_WORKERS))
        threads = os.getenv("WEB_THREADS", str(_DEFAULT_THREADS))
        worker_class = os.getenv("WEB_WORKER_CLASS", _DEFAULT_WORKER_CLASS)
        timeout = os.getenv("WEB_TIMEOUT", "120")
        worker_connections = os.getenv("WEB_WORKER_CONNECTIONS", "1000")

        # Gunicorn reads sys.argv — replace it with our own flags.
        import sys

        sys.argv = [
            "gunicorn",
            "--bind", f"{args.host}:{args.port}",
            "--workers", workers,
            "--worker-class", worker_class,
            "--timeout", timeout,
            "--worker-connections", worker_connections,
            "--access-logfile", "-",
            "--error-logfile", "-",
            "ph_stocks_advisor.web.app:create_app()",
        ]

        # --threads is only relevant for gthread workers.
        if worker_class == "gthread":
            sys.argv.insert(-1, "--threads")
            sys.argv.insert(-1, threads)
        WSGIApplication("%(prog)s [OPTIONS] [APP_MODULE]").run()


if __name__ == "__main__":
    main()
