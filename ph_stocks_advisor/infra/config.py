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

    # -- LLM (provider-agnostic) ------------------------------------------------
    # Two providers (openai, anthropic) × three tiers (large, medium, small).
    # ``llm_provider`` is the default provider; each agent is assigned a spec
    # ``[provider:]tier`` (see AGENT_LLM_SPECS below) so individual agents can
    # run on different providers and tiers. ``build_chat_model`` resolves a
    # spec to a concrete LangChain model.
    llm_provider: str = os.getenv("LLM_PROVIDER", "openai").lower()
    llm_temperature: float = float(os.getenv("LLM_TEMPERATURE", os.getenv("OPENAI_TEMPERATURE", "0.2")))
    # Anthropic models reject a caller-set max_tokens default of 1024 for long
    # structured output — give the consolidator room. OpenAI ignores this.
    llm_max_tokens: int = int(os.getenv("LLM_MAX_TOKENS", "4096"))

    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")

    # Six model IDs. The OpenAI tiers fall back to the legacy OPENAI_MODEL /
    # OPENAI_MINI_MODEL vars so existing deployments keep their pinned models.
    openai_model_large: str = os.getenv("OPENAI_MODEL_LARGE", os.getenv("OPENAI_MODEL", "gpt-4o"))
    openai_model_medium: str = os.getenv("OPENAI_MODEL_MEDIUM", "gpt-4o-mini")
    openai_model_small: str = os.getenv("OPENAI_MODEL_SMALL", os.getenv("OPENAI_MINI_MODEL", "gpt-4o-mini"))
    anthropic_model_large: str = os.getenv("ANTHROPIC_MODEL_LARGE", "claude-opus-4-8")
    anthropic_model_medium: str = os.getenv("ANTHROPIC_MODEL_MEDIUM", "claude-sonnet-5")
    anthropic_model_small: str = os.getenv("ANTHROPIC_MODEL_SMALL", "claude-haiku-4-5")

    # Legacy aliases kept so any external reference still resolves.
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    openai_mini_model: str = os.getenv("OPENAI_MINI_MODEL", "gpt-4o-mini")
    temperature: float = float(os.getenv("OPENAI_TEMPERATURE", "0.2"))

    # -- Tavily web search -----------------------------------------------------
    tavily_api_key: str = os.getenv("TAVILY_API_KEY", "")
    tavily_max_results: int = int(os.getenv("TAVILY_MAX_RESULTS", "5"))
    tavily_search_depth: str = os.getenv("TAVILY_SEARCH_DEPTH", "basic")

    # -- Verdict score (0–100 avoid→buy scale) -----------------------------------
    # The consolidator LLM emits six per-dimension sub-scores; the final
    # score is their weighted average using these env-tunable weights
    # (normalised by their sum, so they need not add to exactly 1).
    score_weight_price: float = float(os.getenv("SCORE_WEIGHT_PRICE", "0.15"))
    score_weight_valuation: float = float(os.getenv("SCORE_WEIGHT_VALUATION", "0.25"))
    score_weight_dividend: float = float(os.getenv("SCORE_WEIGHT_DIVIDEND", "0.15"))
    score_weight_movement: float = float(os.getenv("SCORE_WEIGHT_MOVEMENT", "0.15"))
    score_weight_controversy: float = float(os.getenv("SCORE_WEIGHT_CONTROVERSY", "0.15"))
    score_weight_sentiment: float = float(os.getenv("SCORE_WEIGHT_SENTIMENT", "0.15"))
    # Scores at or above this threshold map to the legacy binary BUY verdict.
    buy_score_threshold: int = int(os.getenv("BUY_SCORE_THRESHOLD", "60"))

    # -- Database --------------------------------------------------------------
    db_backend: str = os.getenv("DB_BACKEND", "sqlite")
    sqlite_path: str = os.getenv("SQLITE_PATH", "reports.db")
    postgres_dsn: str = os.getenv(
        "POSTGRES_DSN",
        "postgresql://localhost:5432/ph_advisor",
    )

    # -- Redis / Celery --------------------------------------------------------
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    # -- MCP server (required) -------------------------------------------------
    # All data-fetching tools dispatch through the PH Stocks Advisor MCP
    # server. There is no in-process fallback — a missing value is a
    # hard configuration error raised by ``mcp_client.get_client()``.
    mcp_server_url: str = os.getenv("MCP_SERVER_URL", "")

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

    # -- Passkeys / WebAuthn ---------------------------------------------------
    # RP ID is the domain passkeys are bound to (host only, no scheme/port).
    # ORIGIN is the exact https origin used to verify ceremonies server-side
    # (never inferred from the request, for safety behind the cloudflared tunnel).
    # Dev defaults target localhost so passkeys work in a local secure context.
    # Off by default (empty RP ID). Enable locally with WEBAUTHN_RP_ID=localhost;
    # in prod set WEBAUTHN_RP_ID=phstockadvisor.sakayandgo.com and the matching origin.
    webauthn_rp_id: str = os.getenv("WEBAUTHN_RP_ID", "")
    webauthn_rp_name: str = os.getenv("WEBAUTHN_RP_NAME", "PH Stock Advisor AI")
    webauthn_origin: str = os.getenv("WEBAUTHN_ORIGIN", "http://localhost:5180")

    @property
    def passkey_enabled(self) -> bool:
        """True when passkey sign-in is turned on (RP ID + origin configured)."""
        return bool(self.webauthn_rp_id and self.webauthn_origin)

    @property
    def auth_enabled(self) -> bool:
        """True when at least one sign-in method is configured."""
        ms_ok = self.entra_client_id and self.entra_client_id != "NOTSET"
        g_ok = self.google_client_id and self.google_client_id != "NOTSET"
        return bool(ms_ok or g_ok or self.passkey_enabled)

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


# Valid provider / tier tokens for an agent spec.
_LLM_PROVIDERS = ("openai", "anthropic")
_LLM_TIERS = ("large", "medium", "small")

# Default agent → spec assignment. Each value is a ``[provider:]tier`` string;
# the provider defaults to ``settings.llm_provider`` when omitted. Consolidation
# and portfolio advice want the strongest tier; the six specialists run small.
# Override any of these with the matching ``LLM_<AGENT>`` env var.
AGENT_LLM_SPECS: dict[str, str] = {
    "consolidator": os.getenv("LLM_CONSOLIDATOR", "large"),
    "portfolio": os.getenv("LLM_PORTFOLIO", "large"),
    "price_agent": os.getenv("LLM_PRICE_AGENT", "small"),
    "dividend_agent": os.getenv("LLM_DIVIDEND_AGENT", "small"),
    "movement_agent": os.getenv("LLM_MOVEMENT_AGENT", "small"),
    "valuation_agent": os.getenv("LLM_VALUATION_AGENT", "small"),
    "controversy_agent": os.getenv("LLM_CONTROVERSY_AGENT", "small"),
    "sentiment_agent": os.getenv("LLM_SENTIMENT_AGENT", "small"),
}


def _resolve_spec(spec: str, settings: Settings) -> tuple[str, str]:
    """Parse a ``[provider:]tier`` spec into ``(provider, tier)``.

    A bare tier inherits ``settings.llm_provider``. Raises ``ValueError`` on an
    unknown provider or tier so a typo fails fast instead of silently picking a
    wrong model.
    """
    raw = (spec or "").strip().lower()
    if ":" in raw:
        provider, tier = raw.split(":", 1)
    else:
        provider, tier = settings.llm_provider, raw
    if provider not in _LLM_PROVIDERS:
        raise ValueError(f"Unknown LLM provider {provider!r} (expected one of {_LLM_PROVIDERS})")
    if tier not in _LLM_TIERS:
        raise ValueError(f"Unknown LLM tier {tier!r} (expected one of {_LLM_TIERS})")
    return provider, tier


def build_chat_model(spec: str, settings: Settings | None = None) -> BaseChatModel:
    """Build a LangChain chat model from a ``[provider:]tier`` spec.

    Returns the abstract ``BaseChatModel`` so callers never depend on a
    concrete provider (Liskov Substitution Principle). Fails fast with a
    clear error when the chosen provider's API key is unset.
    """
    s = settings or get_settings()
    provider, tier = _resolve_spec(spec, s)

    if provider == "openai":
        if not s.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required for an OpenAI LLM spec but is not set.")
        model = {"large": s.openai_model_large, "medium": s.openai_model_medium, "small": s.openai_model_small}[tier]
        return ChatOpenAI(model=model, temperature=s.llm_temperature, api_key=s.openai_api_key)  # type: ignore[arg-type]

    # anthropic
    if not s.anthropic_api_key:
        raise ValueError("ANTHROPIC_API_KEY is required for an Anthropic LLM spec but is not set.")
    from langchain_anthropic import ChatAnthropic

    model = {"large": s.anthropic_model_large, "medium": s.anthropic_model_medium, "small": s.anthropic_model_small}[
        tier
    ]
    # Deliberately omit ``temperature``: current Claude models (Opus 4.8,
    # Sonnet 5, …) reject a caller-set temperature with a 400.
    return ChatAnthropic(model=model, max_tokens=s.llm_max_tokens, api_key=s.anthropic_api_key)  # type: ignore[call-arg]


def get_agent_llm(agent: str, settings: Settings | None = None) -> BaseChatModel:
    """Build the chat model assigned to *agent* via ``AGENT_LLM_SPECS``."""
    spec = AGENT_LLM_SPECS.get(agent, "medium")
    return build_chat_model(spec, settings)


def get_llm(settings: Settings | None = None) -> BaseChatModel:
    """Return the primary (heavy) LLM — the consolidator's assigned model.

    Back-compat shim over :func:`get_agent_llm`; used for consolidation and
    portfolio advice.
    """
    return get_agent_llm("consolidator", settings)


def get_mini_llm(settings: Settings | None = None) -> BaseChatModel:
    """Return a lighter LLM (the active provider's ``small`` tier).

    Back-compat shim; specialist nodes now resolve their own per-agent model
    via :func:`get_agent_llm`.
    """
    return build_chat_model("small", settings)


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
