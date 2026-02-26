"""
Configuration and dependency wiring.

Single Responsibility: only manages settings and shared resources.
"""

from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

from ph_stocks_advisor.infra.repository import AbstractReportRepository

load_dotenv()


class Settings:
    """Application settings read from environment variables."""

    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    temperature: float = float(os.getenv("OPENAI_TEMPERATURE", "0.2"))

    # Tavily web search
    tavily_api_key: str = os.getenv("TAVILY_API_KEY", "")

    # Database — "sqlite" (dev) or "postgres" (prod)
    db_backend: str = os.getenv("DB_BACKEND", "sqlite")
    sqlite_path: str = os.getenv("SQLITE_PATH", "reports.db")
    postgres_dsn: str = os.getenv(
        "POSTGRES_DSN",
        "postgresql://localhost:5432/ph_advisor",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def get_llm(settings: Settings | None = None) -> ChatOpenAI:
    """Return a configured ChatOpenAI instance."""
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
