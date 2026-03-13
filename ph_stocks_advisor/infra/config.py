"""
Configuration and dependency wiring.

Single Responsibility: only manages settings and shared resources.
All user-tunable values live here as environment-variable-backed
class attributes so they can be changed via ``.env`` without touching code.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import os
import re as _re
from functools import lru_cache
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from ph_stocks_advisor.infra.repository import AbstractReportRepository

load_dotenv()


class Settings:
    """Application settings read from environment variables.

    Every attribute has a sensible default so the app runs out of the box
    with just ``OPENAI_API_KEY`` set.
    """

    # -- LLM -------------------------------------------------------------------
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    openai_mini_model: str = os.getenv("OPENAI_MINI_MODEL", "gpt-4o-mini")
    temperature: float = float(os.getenv("OPENAI_TEMPERATURE", "0.2"))

    # -- Tavily web search -----------------------------------------------------
    tavily_api_key: str = os.getenv("TAVILY_API_KEY", "")
    tavily_max_results: int = int(os.getenv("TAVILY_MAX_RESULTS", "5"))
    tavily_search_depth: str = os.getenv("TAVILY_SEARCH_DEPTH", "basic")

    # -- Database --------------------------------------------------------------
    db_backend: str = os.getenv("DB_BACKEND", "sqlite")
    sqlite_path: str = os.getenv("SQLITE_PATH", "reports.db")
    postgres_dsn: str = os.getenv(
        "POSTGRES_DSN",
        "postgresql://localhost:5432/ph_advisor",
    )

    # -- Redis / Celery --------------------------------------------------------
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    # -- API base URLs ---------------------------------------------------------
    dragonfi_base_url: str = os.getenv("DRAGONFI_BASE_URL", "https://api.dragonfi.ph/api/v2")
    pse_edge_base_url: str = os.getenv("PSE_EDGE_BASE_URL", "https://edge.pse.com.ph")
    tradingview_scanner_url: str = os.getenv(
        "TRADINGVIEW_SCANNER_URL",
        "https://scanner.tradingview.com/philippines/scan",
    )

    # -- Timezone ---------------------------------------------------------------
    timezone: str = os.getenv("TIMEZONE", "Asia/Manila")

    # -- Output directory (used as default base for exported files) -------------
    output_dir: str = os.getenv("OUTPUT_DIR", "")

    # -- Microsoft Entra ID (Azure AD) -----------------------------------------
    entra_client_id: str = os.getenv("ENTRA_CLIENT_ID", "")
    entra_client_secret: str = os.getenv("ENTRA_CLIENT_SECRET", "")
    entra_tenant_id: str = os.getenv("ENTRA_TENANT_ID", "common")
    entra_redirect_path: str = os.getenv("ENTRA_REDIRECT_PATH", "/auth/callback")
    flask_secret_key: str = os.getenv("FLASK_SECRET_KEY", "ph-stocks-advisor-change-me-in-production")

    @property
    def entra_authority(self) -> str:
        return f"https://login.microsoftonline.com/{self.entra_tenant_id}"

    # -- Google OAuth2 ---------------------------------------------------------
    google_client_id: str = os.getenv("GOOGLE_CLIENT_ID", "")
    google_client_secret: str = os.getenv("GOOGLE_CLIENT_SECRET", "")
    google_redirect_path: str = os.getenv("GOOGLE_REDIRECT_PATH", "/auth/google/callback")

    @property
    def auth_enabled(self) -> bool:
        """True when at least one identity provider is configured."""
        ms_ok = self.entra_client_id and self.entra_client_id != "NOTSET"
        g_ok = self.google_client_id and self.google_client_id != "NOTSET"
        return bool(ms_ok or g_ok)

    @property
    def google_enabled(self) -> bool:
        return bool(self.google_client_id and self.google_client_id != "NOTSET")

    @property
    def entra_enabled(self) -> bool:
        return bool(self.entra_client_id and self.entra_client_id != "NOTSET")

    # -- HTTP timeouts (seconds) -----------------------------------------------
    http_timeout: int = int(os.getenv("HTTP_TIMEOUT", "15"))

    # -- Analysis thresholds ---------------------------------------------------
    # Trend classification (movement_service)
    trend_up_threshold: float = float(os.getenv("TREND_UP_THRESHOLD", "5"))
    trend_down_threshold: float = float(os.getenv("TREND_DOWN_THRESHOLD", "-5"))

    # Spike detection (controversy_service)
    spike_std_multiplier: float = float(os.getenv("SPIKE_STD_MULTIPLIER", "3"))
    spike_min_abs_return: float = float(os.getenv("SPIKE_MIN_ABS_RETURN", "0.05"))
    high_volatility_threshold: float = float(os.getenv("HIGH_VOLATILITY_THRESHOLD", "0.03"))
    overvaluation_multiplier: float = float(os.getenv("OVERVALUATION_MULTIPLIER", "1.3"))
    distress_multiplier: float = float(os.getenv("DISTRESS_MULTIPLIER", "0.7"))

    # Price catalyst detection (price_service)
    catalyst_yield_threshold: float = float(os.getenv("CATALYST_YIELD_THRESHOLD", "3.0"))
    catalyst_range_pct: float = float(os.getenv("CATALYST_RANGE_PCT", "65"))
    catalyst_day_change_pct: float = float(os.getenv("CATALYST_DAY_CHANGE_PCT", "0.5"))
    catalyst_near_high_pct: float = float(os.getenv("CATALYST_NEAR_HIGH_PCT", "5"))

    # -- Rate limiting ---------------------------------------------------------
    daily_analysis_limit: int = int(os.getenv("DAILY_ANALYSIS_LIMIT", "5"))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


# ---------------------------------------------------------------------------
# Redis connection pool (shared across threads / Gunicorn workers)
# ---------------------------------------------------------------------------

if TYPE_CHECKING:
    import redis as redis_lib

_redis_pool: redis_lib.ConnectionPool | None = None  # type: ignore[name-defined]
_redis_pool_raw: redis_lib.ConnectionPool | None = None  # type: ignore[name-defined]


def get_redis() -> redis_lib.Redis:  # type: ignore[name-defined]
    """Return a Redis client backed by a shared ``ConnectionPool``.

    The pool is created lazily on first call and reused thereafter,
    keeping the total number of Redis connections bounded regardless
    of how many Gunicorn threads or Flask requests are active.

    Pool size is configurable via ``REDIS_MAX_CONNECTIONS`` (default: 20).
    """
    import redis as redis_lib  # local import to avoid cost at module level

    global _redis_pool
    if _redis_pool is None:
        max_conn = int(os.getenv("REDIS_MAX_CONNECTIONS", "10"))
        _redis_pool = redis_lib.ConnectionPool.from_url(
            get_settings().redis_url,
            max_connections=max_conn,
            decode_responses=True,
        )
    return redis_lib.Redis(connection_pool=_redis_pool)


def get_redis_raw() -> redis_lib.Redis:  # type: ignore[name-defined]
    """Return a Redis client that does **not** decode responses.

    Flask-Session (and any other consumer that stores binary / pickled
    data) must use this client.  The pool is separate from the
    ``decode_responses=True`` pool returned by :func:`get_redis`.
    """
    import redis as redis_lib

    global _redis_pool_raw
    if _redis_pool_raw is None:
        max_conn = int(os.getenv("REDIS_MAX_CONNECTIONS", "10"))
        _redis_pool_raw = redis_lib.ConnectionPool.from_url(
            get_settings().redis_url,
            max_connections=max_conn,
            decode_responses=False,
        )
    return redis_lib.Redis(connection_pool=_redis_pool_raw)


def _parse_tz(name: str) -> dt.tzinfo:
    """Parse a timezone string into a :class:`datetime.tzinfo`.

    Supports:
    * IANA names  – ``Asia/Manila``, ``US/Eastern``, ``UTC``
    * Offset form – ``UTC+8``, ``GMT+8``, ``UTC-5``, ``GMT-05:30``
    """
    m = _re.match(r"^(?:UTC|GMT)([+-])(\d{1,2})(?::(\d{2}))?$", name, _re.IGNORECASE)
    if m:
        sign = 1 if m.group(1) == "+" else -1
        hours = int(m.group(2))
        minutes = int(m.group(3) or 0)
        return dt.timezone(dt.timedelta(hours=sign * hours, minutes=sign * minutes))
    return ZoneInfo(name)


def get_today() -> dt.date:
    """Return today's date in the user-configured timezone."""
    tz = _parse_tz(get_settings().timezone)
    return dt.datetime.now(tz=tz).date()


def get_llm(settings: Settings | None = None) -> BaseChatModel:
    """Return the *primary* (heavy) LLM instance.

    Use this for tasks that require deep reasoning such as the
    consolidator agent.  Returns the abstract ``BaseChatModel`` so
    callers never depend on a concrete provider (Liskov Substitution
    Principle).
    """
    s = settings or get_settings()
    return ChatOpenAI(
        model=s.openai_model,
        temperature=s.temperature,
        api_key=s.openai_api_key,  # type: ignore[arg-type]
    )


def get_mini_llm(settings: Settings | None = None) -> BaseChatModel:
    """Return a *lighter* LLM for simpler specialist tasks.

    Configured via ``OPENAI_MINI_MODEL``.  Falls back to the primary
    model when the env var is not set.
    """
    s = settings or get_settings()
    return ChatOpenAI(
        model=s.openai_mini_model,
        temperature=s.temperature,
        api_key=s.openai_api_key,  # type: ignore[arg-type]
    )


_repository: AbstractReportRepository | None = None


def get_repository(settings: Settings | None = None) -> AbstractReportRepository:
    """Return a **shared** repository instance (singleton).

    The repository is created and initialised once, then reused for
    every subsequent call.  This is critical for performance: the
    PostgreSQL backend maintains a ``ThreadedConnectionPool`` that
    borrows / returns connections automatically — creating a new
    repository per request would spin up a new pool each time and
    exhaust database connections under load.

    Follows the Dependency Inversion Principle: callers receive an
    abstract interface, never a concrete class.
    """
    global _repository
    if _repository is not None:
        return _repository

    s = settings or get_settings()
    if s.db_backend.lower() == "postgres":
        from ph_stocks_advisor.infra.repository_postgres import PostgresReportRepository

        repo = PostgresReportRepository(dsn=s.postgres_dsn)
    else:
        from ph_stocks_advisor.infra.repository_sqlite import SQLiteReportRepository

        repo = SQLiteReportRepository(db_path=s.sqlite_path)
    repo.initialize()
    _repository = repo
    return repo


def _reset_repository() -> None:
    """Close and discard the cached repository (for testing only)."""
    global _repository
    if _repository is not None:
        with contextlib.suppress(Exception):
            _repository.close()
        _repository = None
