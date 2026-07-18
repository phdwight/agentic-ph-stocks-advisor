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

import json
import logging
import secrets
import tomllib
import uuid
from datetime import UTC, datetime, timedelta, timezone
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path

from flask import Flask, Response, abort, jsonify, render_template, request, session
from markupsafe import Markup
from werkzeug.middleware.proxy_fix import ProxyFix

from ph_stocks_advisor.export.formatter import (
    DATA_SOURCES,
    DISCLAIMER,
    format_timestamp,
    parse_sections,
)
from ph_stocks_advisor.export.html import _body_to_html
from ph_stocks_advisor.infra import trading_calendar
from ph_stocks_advisor.infra.config import get_redis, get_repository, get_settings
from ph_stocks_advisor.web.auth import auth_bp, get_current_user, login_required
from ph_stocks_advisor.web.rate_limit import release as rl_release
from ph_stocks_advisor.web.rate_limit import reserve as rl_reserve

logger = logging.getLogger(__name__)


# Cache the resolved version so we don't re-read pyproject.toml on every
# request — the value is fixed for the lifetime of the process.
_VERSION_CACHE: str | None = None


def _read_pyproject_version() -> str | None:
    """Return the version declared in the repo's ``pyproject.toml``.

    The web footer must reflect the *current* source revision so that
    deployed releases always show their published version (e.g. when
    the installed egg-info lags behind ``pyproject.toml`` after a
    bump but before reinstall). Returns ``None`` when the file is not
    reachable (e.g. installed wheel without source layout).
    """
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    if not pyproject.is_file():
        return None
    try:
        with pyproject.open("rb") as fh:
            data = tomllib.load(fh)
    except OSError, tomllib.TOMLDecodeError:
        return None
    project = data.get("project")
    if not isinstance(project, dict):
        return None
    version = project.get("version")
    return version if isinstance(version, str) and version else None


def _app_version() -> str:
    global _VERSION_CACHE
    if _VERSION_CACHE is not None:
        return _VERSION_CACHE
    # Prefer pyproject.toml (source of truth in the repo) so that the
    # footer matches the latest release even when the installed
    # distribution's metadata is stale.
    version = _read_pyproject_version()
    if version is None:
        try:
            version = _pkg_version("ph-stocks-advisor")
        except PackageNotFoundError:
            version = "dev"
    _VERSION_CACHE = version
    return version


# Philippine Stock Exchange timezone (UTC+8).
_PHT = timezone(timedelta(hours=8))
# Daily cutoff hour in PHT — reports generated before this are stale.
_CUTOFF_HOUR_PHT = 15  # 3:00 PM

# Redis key prefix for in-flight analysis dedup locks.
_INFLIGHT_PREFIX = "analysis:inflight:"
# Reverse mapping: task_id -> symbol, for O(1) cancel lookup.
_INFLIGHT_TASK_PREFIX = "analysis:task:"
# Portfolio in-flight lock: portfolio:inflight:{user}:{symbol} -> task_id
_PORTFOLIO_INFLIGHT_PREFIX = "portfolio:inflight:"
# How long the lock lives before auto-expiring (seconds).
_INFLIGHT_TTL = 10 * 60  # 10 minutes


def _last_cutoff() -> datetime:
    """Most recent trading-day 3:00 PM PHT close as UTC (skips weekends).

    Reports created on/after this instant are fresh. See
    :mod:`ph_stocks_advisor.infra.trading_calendar`.
    """
    return trading_calendar.last_trading_close()


def _next_cutoff() -> datetime:
    """Next trading-day 3:00 PM PHT close as UTC."""
    return trading_calendar.next_trading_close()


def _is_past_cutoff() -> bool:
    """Return True if the current PHT time is at or past the 3 PM close hour."""
    return datetime.now(tz=_PHT).hour >= _CUTOFF_HOUR_PHT


def _cutoff_label() -> str:
    """Human label for when the next fresh run becomes available.

    e.g. ``"after today's 3:00 PM PHT close"`` or ``"after Monday's \u2026"`` \u2014
    trading-day aware, so on a Friday evening it points to Monday.
    """
    return trading_calendar.next_close_label()


def create_app() -> Flask:
    """Application factory — returns a configured Flask instance."""
    settings = get_settings()

    # Fail fast if the default secret key is used with auth enabled.
    _DEFAULT_SECRET = "ph-stocks-advisor-change-me-in-production"
    if settings.auth_enabled and settings.flask_secret_key == _DEFAULT_SECRET:
        raise RuntimeError(
            "FLASK_SECRET_KEY must be changed from its default value "
            "when authentication is enabled.  Set a strong random "
            "value via the FLASK_SECRET_KEY environment variable."
        )

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
    app.config["SESSION_COOKIE_HTTPONLY"] = True
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
        logger.warning("Redis unavailable for sessions — using signed-cookie sessions.")

    # Trust reverse-proxy headers (Azure Container Apps, nginx, etc.)
    # so that request.url_root uses https:// when behind TLS termination.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)  # type: ignore[assignment]

    # Register the Entra ID authentication blueprint.
    app.register_blueprint(auth_bp)
    from ph_stocks_advisor.web.passkey import passkey_bp

    app.register_blueprint(passkey_bp)

    # ------------------------------------------------------------------
    # Security headers (CWE-693)
    # ------------------------------------------------------------------

    @app.after_request
    def _set_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        # CSP: allow inline styles/scripts needed by the UI but block
        # everything else.  Tighten further when moving to a build step.
        response.headers.setdefault(
            "Content-Security-Policy",
            (
                "default-src 'self'; "
                "script-src 'self'; "
                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                "font-src 'self' https://fonts.gstatic.com; "
                "img-src 'self' data:; "
                "connect-src 'self'; "
                "frame-ancestors 'self'"
            ),
        )
        if settings.auth_enabled:
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        return response

    # ------------------------------------------------------------------
    # CSRF protection (CWE-352)
    # ------------------------------------------------------------------
    # Routes that are safe to exempt (no user state change, or have
    # their own protection like OAuth state params).
    _CSRF_EXEMPT_ENDPOINTS: set[str | None] = {
        "healthz",
        "auth.callback",
        "auth.google_callback",
        "auth.switch_type",
    }

    def _generate_csrf_token() -> str:
        """Return the CSRF token for the current session, creating one if needed."""
        if "_csrf_token" not in session:
            session["_csrf_token"] = secrets.token_hex(32)
        return session["_csrf_token"]

    @app.before_request
    def _csrf_protect():
        """Validate CSRF token on state-changing requests.

        Checks the ``X-CSRFToken`` header first (used by ``fetch()``
        calls), then falls back to a ``csrf_token`` form field.
        """
        if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
            return None
        if request.endpoint in _CSRF_EXEMPT_ENDPOINTS:
            return None
        # When auth is disabled (local dev), skip CSRF enforcement.
        if not settings.auth_enabled:
            return None

        token = (
            request.headers.get("X-CSRFToken")
            or (request.form.get("csrf_token") if request.form else None)
            or ((request.get_json(silent=True) or {}).get("csrf_token") if request.is_json else None)
        )
        if not token or token != session.get("_csrf_token"):
            logger.warning("CSRF validation failed for %s %s", request.method, request.path)
            abort(403)
        return None

    @app.template_filter("md_to_html")
    def md_to_html_filter(text: str) -> Markup:
        """Convert light-markdown section body to formatted HTML."""
        return Markup(_body_to_html(text))  # noqa: S704

    @app.context_processor
    def inject_user():
        """Make ``current_user``, ``auth_enabled``, ``csrf_token``, and
        ``app_version`` available in every template."""
        return {
            "current_user": get_current_user(),
            "auth_enabled": get_settings().auth_enabled,
            "csrf_token": _generate_csrf_token,
            "app_version": _app_version(),
        }

    @app.context_processor
    def inject_sidebar_history():
        """Provide a date-grouped list of recently analysed tickers for the
        sidebar shown on every page."""
        try:
            user = get_current_user()
            if not user:
                return {"sidebar_history": []}
            repo = get_repository()
            if user.get("email"):
                records = repo.list_user_symbols(user_id=user["email"], limit=30)
            else:
                records = repo.list_recent_symbols(limit=30)
        except Exception:
            return {"sidebar_history": []}

        grouped: list[dict] = []
        current_key: str | None = None
        for r in records:
            created = getattr(r, "created_at", None)
            if created is None:
                continue
            day_key = created.strftime("%Y-%m-%d")
            if day_key != current_key:
                grouped.append({"date": created, "stocks": []})
                current_key = day_key
            grouped[-1]["stocks"].append(r)
        return {"sidebar_history": grouped}

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
        except Exception:
            checks["redis"] = "error"
            healthy = False

        # ── Database ─────────────────────────────────────────────────
        try:
            repo = get_repository()
            repo.list_recent_symbols(limit=1)

            checks["database"] = "ok"
        except Exception:
            checks["database"] = "error"
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
                recent = repo.list_user_symbols(user_id=user["email"], limit=50)
            else:
                recent = repo.list_recent_symbols(limit=50)
        except Exception:
            recent = []
        return render_template("index.html", recent_stocks=recent)

    @app.route("/analyse", methods=["POST"])
    @login_required
    def analyse():
        """Check for a fresh cached report; dispatch to Celery if stale/missing."""
        from ph_stocks_advisor.infra.repository import UserType
        from ph_stocks_advisor.web.tasks import analyse_stock

        symbol = (request.form.get("symbol") or "").strip().upper().replace(".PS", "")
        if not symbol:
            return jsonify({"error": "Symbol is required"}), 400

        # Determine if the current user has elevated privileges.
        user = get_current_user()
        is_elevated = (user or {}).get("user_type", 0) == UserType.ELEVATED

        # Check for a recent report (generated after last 3 PM PHT cutoff).
        # Elevated users bypass the multi-day cache but are still subject
        # to a per-stock daily cooldown (one analysis per trading day).
        repo = get_repository()
        record = repo.get_latest_by_symbol(symbol)

        if record and record.created_at:
            cutoff = _last_cutoff()

            if is_elevated:
                # Elevated cooldown: already analysed since last 3 PM PHT.
                if record.created_at >= cutoff:
                    reset_at = _next_cutoff()
                    logger.info(
                        "Elevated cooldown: %s already analysed since %s, next window %s.",
                        symbol,
                        cutoff.isoformat(),
                        reset_at.isoformat(),
                    )
                    return jsonify(
                        {
                            "error": (f"{symbol} was already analysed today. You can re-analyse {_cutoff_label()}."),
                            "reset_at": reset_at.isoformat(),
                            "report_id": record.id,
                            "symbol": symbol,
                        }
                    ), 429
            else:
                # Normal users: serve the cached report if generated
                # after the most recent 3:00 PM PHT cutoff.
                if record.created_at >= cutoff:
                    logger.info(
                        "Fresh report found for %s (since %s), serving cached.",
                        symbol,
                        cutoff.isoformat(),
                    )
                    # Track symbol for the current user.
                    if user and user.get("email"):
                        try:
                            repo2 = get_repository()
                            repo2.add_user_symbol(user["email"], symbol)
                        except Exception:
                            logger.debug("Failed to record user-symbol link.")
                    return jsonify(
                        {
                            "status": "cached",
                            "symbol": symbol,
                            "report_id": record.id,
                        }
                    )

        # A new run is warranted (report is stale or missing), but the PSE
        # session is live — no runs during 9 AM–3 PM PHT. Serve the most
        # recent report if we have one (the report page notes it's pre-close);
        # otherwise a fresh analysis becomes available after today's close.
        if trading_calendar.is_market_open():
            if record and record.created_at:
                return jsonify({"status": "cached", "symbol": symbol, "report_id": record.id})
            return jsonify(
                {
                    "error": (
                        f"The market is open (9 AM–3 PM PHT). A fresh analysis for {symbol} "
                        f"will be available {trading_calendar.next_close_label()}."
                    ),
                    "reset_at": trading_calendar.next_trading_close().isoformat(),
                    "symbol": symbol,
                }
            ), 425

        # No fresh report — claim the in-flight dedup lock ATOMICALLY.
        # When several users ask for the same symbol at once, exactly one
        # request may dispatch a run; the rest join the winner's task and
        # stream its progress (same result, no duplicate token spend).
        # SET NX is the claim — unlike a GET-then-SET sequence it cannot
        # race. The task id is generated up-front so the lock value is
        # final from the moment the claim lands (no placeholder state).
        r = get_redis()
        inflight_key = f"{_INFLIGHT_PREFIX}{symbol}"
        my_task_id = str(uuid.uuid4())
        if not r.set(inflight_key, my_task_id, nx=True, ex=_INFLIGHT_TTL):
            existing_task_id = r.get(inflight_key)
            if existing_task_id:
                logger.info(
                    "In-flight analysis found for %s (task %s), joining.",
                    symbol,
                    existing_task_id,
                )
                # The joiner asked for this ticker too — track it for them.
                if user and user.get("email"):
                    try:
                        get_repository().add_user_symbol(user["email"], symbol)
                    except Exception:
                        logger.debug("Failed to record user-symbol link.")
                return jsonify(
                    {
                        "status": "joined",
                        "symbol": symbol,
                        "task_id": existing_task_id,
                    }
                )
            # Rare: the lock vanished between the failed claim and the read —
            # the in-flight run just finished (or was cancelled). If it saved
            # a report, serve that; otherwise let the user retry.
            finished = repo.get_latest_by_symbol(symbol)
            if finished and finished.id:
                return jsonify({"status": "cached", "symbol": symbol, "report_id": finished.id})
            return jsonify({"error": f"{symbol} just finished processing — please try again."}), 409

        # --- Per-user daily rate limit (atomic reserve) ----------------
        # Elevated users are exempt from the daily analysis limit.
        user_id = (user or {}).get("email", "anonymous")
        if not is_elevated:
            allowed, count = rl_reserve(r, user_id, settings.daily_analysis_limit)
            if not allowed:
                # We hold the inflight claim but won't run — release it so
                # another user's request for this symbol can proceed.
                r.delete(inflight_key)
                logger.warning(
                    "User %s exceeded daily analysis limit (%d/%d).",
                    user_id,
                    count,
                    settings.daily_analysis_limit,
                )
                next_reset = _next_cutoff()
                return jsonify(
                    {
                        "error": (
                            f"Daily analysis limit reached ({settings.daily_analysis_limit} per day). "
                            f"Your quota resets {_cutoff_label()}."
                        ),
                        "reset_at": next_reset.isoformat(),
                    }
                ), 429

        # Dispatch analysis to the Celery worker under the pre-generated
        # task id (the one already stored in the inflight lock, so joiners
        # stream the right task). If dispatch fails, release both the
        # claim and the reserved quota slot — nothing ran.
        try:
            analyse_stock.apply_async(args=[symbol], kwargs={"user_id": user_id}, task_id=my_task_id)
        except Exception:
            r.delete(inflight_key)
            if not is_elevated:
                rl_release(r, user_id)
            raise

        # Reverse mapping for O(1) cancel lookup (avoids scan_iter)
        r.set(f"{_INFLIGHT_TASK_PREFIX}{my_task_id}", symbol, ex=_INFLIGHT_TTL)

        # Track symbol for the current user.
        if user and user.get("email"):
            try:
                repo2 = get_repository()
                repo2.add_user_symbol(user["email"], symbol)
            except Exception:
                logger.debug("Failed to record user-symbol link.")

        return jsonify({"status": "started", "symbol": symbol, "task_id": my_task_id})

    @app.route("/status/<task_id>")
    @login_required
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
            return jsonify(
                {
                    "state": "SUCCESS",
                    "done": True,
                    "symbol": data.get("symbol", ""),
                    "verdict": data.get("verdict", ""),
                    "report_id": data.get("report_id"),
                    "error": data.get("error"),
                }
            )

        if result.state == "FAILURE":
            return jsonify(
                {
                    "state": "FAILURE",
                    "done": True,
                    "error": str(result.info),
                }
            )

        if result.state == "REVOKED":
            return jsonify(
                {
                    "state": "REVOKED",
                    "done": True,
                    "error": "Analysis was cancelled.",
                }
            )

        # RETRY, etc.
        return jsonify({"state": result.state, "done": False})

    @app.route("/stream/<task_id>")
    @login_required
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
    @login_required
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
            is_cached = record.created_at >= _last_cutoff()

        # A stale report shown during live market hours: the page notes that a
        # fresh run is deferred until after the 3 PM PHT close.
        market_open_stale = (not is_cached) and trading_calendar.is_market_open()

        # Verdict score (0–100 avoid→buy) drives the meter. Legacy rows
        # created before scoring have no score — fall back to the old
        # fixed marker positions and the binary verdict word.
        from ph_stocks_advisor.data.models import score_band

        verdict_score = record.score
        if verdict_score is not None:
            verdict_band = score_band(verdict_score)
            marker_pct = verdict_score
            if verdict_band in ("BUY", "STRONG BUY"):
                band_class = "buy"
            elif verdict_band == "WAIT":
                band_class = "wait"
            else:
                band_class = "avoid"
        else:
            verdict_band = None
            marker_pct = 80 if is_buy else 18
            band_class = "buy" if is_buy else "nb"

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
        portfolio_inflight_task_id: str | None = None
        if is_elevated and user and user.get("email"):
            try:
                user_holding = repo.get_holding(user["email"], symbol)
                portfolio_report = repo.get_portfolio_report(user["email"], symbol)
                # Check if portfolio analysis is on cooldown (already run since last cutoff).
                if portfolio_report and portfolio_report.created_at:
                    portfolio_on_cooldown = portfolio_report.created_at >= _last_cutoff()
            except Exception:
                logger.debug("Could not load holding/portfolio for %s", symbol)

            # Check for an in-flight portfolio analysis so the page shows
            # a spinner instead of a stale report on refresh.
            try:
                r = get_redis()
                pf_key = f"{_PORTFOLIO_INFLIGHT_PREFIX}{user['email']}:{symbol}"
                inflight_tid = r.get(pf_key)
                if inflight_tid:
                    portfolio_inflight_task_id = str(inflight_tid)
            except Exception:
                logger.debug("Could not check portfolio inflight for %s", symbol)

        # Is portfolio analysis gated (before 3 PM PHT)?
        portfolio_before_cutoff = is_elevated and not _is_past_cutoff()

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
            portfolio_before_cutoff=portfolio_before_cutoff,
            portfolio_inflight_task_id=portfolio_inflight_task_id,
            next_cutoff_label=_cutoff_label(),
            market_open_stale=market_open_stale,
            verdict_score=verdict_score,
            verdict_band=verdict_band,
            marker_pct=marker_pct,
            band_class=band_class,
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
        return jsonify(
            {
                "holding": {
                    "symbol": holding.symbol,
                    "shares": holding.shares,
                    "avg_cost": holding.avg_cost,
                }
            }
        )

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
        except TypeError, ValueError:
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
        """Trigger a portfolio-aware analysis for the current user's holding.

        Gate: only allowed after 3:00 PM PHT (market close).
        If no base report exists yet, one is dispatched first and the
        portfolio analysis is chained automatically.
        """
        from ph_stocks_advisor.infra.repository import UserType
        from ph_stocks_advisor.web.tasks import analyse_stock, portfolio_analyse_stock

        user = get_current_user()
        if not user or user.get("user_type", 0) != UserType.ELEVATED:
            return jsonify({"error": "Elevated access required"}), 403

        # Only allow after 3:00 PM PHT.
        if not _is_past_cutoff():
            cutoff_pht = datetime.now(tz=_PHT).replace(hour=_CUTOFF_HOUR_PHT, minute=0, second=0, microsecond=0)
            return jsonify(
                {
                    "error": f"Portfolio analysis is available {_cutoff_label()} (after market close).",
                    "available_at": cutoff_pht.astimezone(UTC).isoformat(),
                    "symbol": symbol.upper().replace(".PS", ""),
                }
            ), 425  # 425 Too Early

        symbol = symbol.upper().replace(".PS", "")
        repo = get_repository()

        # Require that the user has a holding saved for this symbol.
        holding = repo.get_holding(user["email"], symbol)
        if holding is None:
            return jsonify({"error": "No holding found for this symbol. Save your position first."}), 400

        # Daily cooldown: one portfolio analysis per stock per day.
        # Resets at 3:00 PM PHT (UTC+8).
        existing_pr = repo.get_portfolio_report(user["email"], symbol)
        if existing_pr and existing_pr.created_at and existing_pr.created_at >= _last_cutoff():
            next_reset = _next_cutoff()
            return jsonify(
                {
                    "error": (
                        f"Portfolio analysis for {symbol} was already run today. You can re-analyse {_cutoff_label()}."
                    ),
                    "reset_at": next_reset.isoformat(),
                    "symbol": symbol,
                }
            ), 429

        # Check for a fresh base report; if missing, dispatch one first.
        record = repo.get_latest_by_symbol(symbol)
        needs_base = record is None or record.created_at is None or record.created_at < _last_cutoff()

        if needs_base:
            # Dispatch base analysis with portfolio analysis linked as a
            # callback.  Using ``apply_async(link=...)`` instead of
            # ``chain(...)`` ensures ``result.id`` is the *base* task's
            # ID, so the inflight dedup lock and SSE/polling streams
            # work correctly when another request joins mid-flight.
            #
            # Pre-assign a task ID for the portfolio callback so the
            # frontend can poll it independently and avoid displaying
            # a stale portfolio report while the callback is still
            # running.
            portfolio_task_id = str(uuid.uuid4())
            base_task = analyse_stock.s(symbol, user_id=user["email"])
            portfolio_task = portfolio_analyse_stock.s(
                user_id=user["email"],
                shares=holding.shares,
                avg_cost=holding.avg_cost,
            ).set(task_id=portfolio_task_id)
            result = base_task.apply_async(link=[portfolio_task])

            # Store inflight lock for the base analysis dedup.
            r = get_redis()
            inflight_key = f"{_INFLIGHT_PREFIX}{symbol}"
            r.set(inflight_key, result.id, ex=_INFLIGHT_TTL)
            r.set(f"{_INFLIGHT_TASK_PREFIX}{result.id}", symbol, ex=_INFLIGHT_TTL)

            # Track portfolio analysis in-flight so a page refresh shows
            # the spinner instead of a stale report.
            pf_key = f"{_PORTFOLIO_INFLIGHT_PREFIX}{user['email']}:{symbol}"
            r.set(pf_key, portfolio_task_id, ex=_INFLIGHT_TTL)

            return jsonify(
                {
                    "status": "started",
                    "task_id": result.id,
                    "portfolio_task_id": portfolio_task_id,
                    "symbol": symbol,
                    "chained": True,
                }
            )

        # Base report exists and is fresh — dispatch portfolio analysis only.
        assert record is not None  # guaranteed: needs_base was False
        task = portfolio_analyse_stock.delay(
            {},  # empty preceding-result placeholder
            symbol=symbol,
            user_id=user["email"],
            shares=holding.shares,
            avg_cost=holding.avg_cost,
            base_report_id=record.id,
        )

        # Track portfolio analysis in-flight.
        r = get_redis()
        pf_key = f"{_PORTFOLIO_INFLIGHT_PREFIX}{user['email']}:{symbol}"
        r.set(pf_key, task.id, ex=_INFLIGHT_TTL)

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
        return jsonify(
            {
                "report": {
                    "id": pr.id,
                    "symbol": pr.symbol,
                    "shares": pr.shares,
                    "avg_cost": pr.avg_cost,
                    "analysis": pr.analysis,
                    "analysis_html": _body_to_html(pr.analysis) if pr.analysis else "",
                    "created_at": pr.created_at.isoformat() if pr.created_at else None,
                }
            }
        )

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

    from ph_stocks_advisor.infra.logging import LOG_FORMAT_GUNICORN_ACCESS, configure_logging

    configure_logging()

    parser = argparse.ArgumentParser(description="PH Stocks Advisor Web UI")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    parser.add_argument("--port", type=int, default=5000, help="Port to bind to")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    args = parser.parse_args()

    if args.debug:
        # Development: use Flask's built-in server with auto-reload.
        logger.warning("Running in DEBUG mode with Werkzeug interactive debugger. Never use --debug in production.")
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
            "--bind",
            f"{args.host}:{args.port}",
            "--workers",
            workers,
            "--worker-class",
            worker_class,
            "--timeout",
            timeout,
            "--worker-connections",
            worker_connections,
            "--access-logfile",
            "-",
            "--error-logfile",
            "-",
            "--access-logformat",
            LOG_FORMAT_GUNICORN_ACCESS,
            "ph_stocks_advisor.web.app:create_app()",
        ]

        # --threads is only relevant for gthread workers.
        if worker_class == "gthread":
            sys.argv.insert(-1, "--threads")
            sys.argv.insert(-1, threads)
        WSGIApplication("%(prog)s [OPTIONS] [APP_MODULE]").run()


if __name__ == "__main__":
    main()
