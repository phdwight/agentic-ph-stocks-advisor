"""
Configuration and dependency wiring.

Single Responsibility: only manages settings and shared resources.
All user-tunable values live here as environment-variable-backed
class attributes so they can be changed via ``.env`` without touching code.
"""

from __future__ import annotations

import datetime as dt
import os
import re as _re
from functools import lru_cache
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
    dragonfi_base_url: str = os.getenv(
        "DRAGONFI_BASE_URL", "https://api.dragonfi.ph/api/v2"
    )
    pse_edge_base_url: str = os.getenv(
        "PSE_EDGE_BASE_URL", "https://edge.pse.com.ph"
    )
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
    flask_secret_key: str = os.getenv(
        "FLASK_SECRET_KEY", "ph-stocks-advisor-change-me-in-production"
    )

    @property
    def entra_authority(self) -> str:
        return f"https://login.microsoftonline.com/{self.entra_tenant_id}"

    # -- HTTP timeouts (seconds) -----------------------------------------------
    http_timeout: int = int(os.getenv("HTTP_TIMEOUT", "15"))

    # -- Analysis thresholds ---------------------------------------------------
    # Trend classification (movement_service)
    trend_up_threshold: float = float(os.getenv("TREND_UP_THRESHOLD", "5"))
    trend_down_threshold: float = float(os.getenv("TREND_DOWN_THRESHOLD", "-5"))

    # Spike detection (controversy_service)
    spike_std_multiplier: float = float(os.getenv("SPIKE_STD_MULTIPLIER", "3"))
    spike_min_abs_return: float = float(os.getenv("SPIKE_MIN_ABS_RETURN", "0.05"))
    high_volatility_threshold: float = float(
        os.getenv("HIGH_VOLATILITY_THRESHOLD", "0.03")
    )
    overvaluation_multiplier: float = float(
        os.getenv("OVERVALUATION_MULTIPLIER", "1.3")
    )
    distress_multiplier: float = float(os.getenv("DISTRESS_MULTIPLIER", "0.7"))

    # Price catalyst detection (price_service)
    catalyst_yield_threshold: float = float(
        os.getenv("CATALYST_YIELD_THRESHOLD", "3.0")
    )
    catalyst_range_pct: float = float(os.getenv("CATALYST_RANGE_PCT", "65"))
    catalyst_day_change_pct: float = float(
        os.getenv("CATALYST_DAY_CHANGE_PCT", "0.5")
    )
    catalyst_near_high_pct: float = float(
        os.getenv("CATALYST_NEAR_HIGH_PCT", "5")
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def _parse_tz(name: str) -> dt.tzinfo:
    """Parse a timezone string into a :class:`datetime.tzinfo`.

    Supports:
    * IANA names  – ``Asia/Manila``, ``US/Eastern``, ``UTC``
    * Offset form – ``UTC+8``, ``GMT+8``, ``UTC-5``, ``GMT-05:30``
    """
    m = _re.match(
        r"^(?:UTC|GMT)([+-])(\d{1,2})(?::(\d{2}))?$", name, _re.IGNORECASE
    )
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
    """Return a configured LLM instance.

    Returns the abstract ``BaseChatModel`` so callers never depend on
    a concrete provider (Liskov Substitution Principle).
    """
    s = settings or get_settings()
    return ChatOpenAI(
        model=s.openai_model,
        temperature=s.temperature,
        api_key=s.openai_api_key,  # type: ignore[arg-type]
    )


def get_repository(settings: Settings | None = None) -> AbstractReportRepository:
    """
    Factory that returns the correct repository implementation
    based on the DB_BACKEND environment variable.

    Follows the Dependency Inversion Principle: callers receive an
    abstract interface, never a concrete class.
    """
    s = settings or get_settings()
    if s.db_backend.lower() == "postgres":
        from ph_stocks_advisor.infra.repository_postgres import PostgresReportRepository

        repo = PostgresReportRepository(dsn=s.postgres_dsn)
    else:
        from ph_stocks_advisor.infra.repository_sqlite import SQLiteReportRepository

        repo = SQLiteReportRepository(db_path=s.sqlite_path)
    repo.initialize()
    return repo
