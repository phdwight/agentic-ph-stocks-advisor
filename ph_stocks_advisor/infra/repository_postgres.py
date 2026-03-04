"""
PostgreSQL implementation of the report repository.

Used in production environments.  Requires `psycopg2` (or `psycopg2-binary`).

Uses a **thread-safe connection pool** (``psycopg2.pool.ThreadedConnectionPool``)
so multiple Gunicorn threads / Celery workers share a bounded set of
database connections instead of opening one per request.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Generator, Optional

import psycopg2  # type: ignore[import-untyped]
import psycopg2.extras  # type: ignore[import-untyped]
import psycopg2.pool  # type: ignore[import-untyped]

from ph_stocks_advisor.infra.repository import AbstractReportRepository, ReportRecord, UserRecord

logger = logging.getLogger(__name__)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS reports (
    id                  SERIAL PRIMARY KEY,
    symbol              VARCHAR(20) NOT NULL,
    verdict             VARCHAR(20) NOT NULL,
    summary             TEXT        NOT NULL,
    price_section       TEXT        NOT NULL DEFAULT '',
    dividend_section    TEXT        NOT NULL DEFAULT '',
    movement_section    TEXT        NOT NULL DEFAULT '',
    valuation_section   TEXT        NOT NULL DEFAULT '',
    controversy_section TEXT        NOT NULL DEFAULT '',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

_CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_reports_symbol_created
ON reports (symbol, created_at DESC);
"""

_CREATE_USER_SYMBOLS_SQL = """
CREATE TABLE IF NOT EXISTS user_symbols (
    user_id    VARCHAR(320) NOT NULL,
    symbol     VARCHAR(20)  NOT NULL,
    created_at TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, symbol)
);
"""

_CREATE_USERS_SQL = """
CREATE TABLE IF NOT EXISTS users (
    oid           VARCHAR(320) PRIMARY KEY,
    name          VARCHAR(320) NOT NULL DEFAULT '',
    email         VARCHAR(320) NOT NULL DEFAULT '',
    provider      VARCHAR(20)  NOT NULL DEFAULT '',
    user_type     INTEGER      NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    last_login_at TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
"""

# ── Schema migrations (idempotent) ──────────────────────────────────────────
_MIGRATIONS_SQL = [
    # Added in v2 — user_type column for NORMAL(0)/ELEVATED(1) privileges
    """
    ALTER TABLE users
        ADD COLUMN IF NOT EXISTS user_type INTEGER NOT NULL DEFAULT 0;
    """,
]


class PostgresReportRepository(AbstractReportRepository):
    """PostgreSQL-backed repository with thread-safe connection pooling.

    Connections are borrowed from a ``ThreadedConnectionPool`` for each
    operation and returned immediately after use, keeping the total
    connection count bounded regardless of how many Gunicorn workers or
    threads are active.

    Pool size is configurable via environment variables:

    * ``PG_POOL_MIN`` — minimum idle connections (default: 2)
    * ``PG_POOL_MAX`` — maximum connections   (default: 10)
    """

    def __init__(
        self,
        dsn: str,
        *,
        min_conn: int | None = None,
        max_conn: int | None = None,
    ) -> None:
        self._dsn = dsn
        self._min_conn = min_conn or int(os.getenv("PG_POOL_MIN", "2"))
        self._max_conn = max_conn or int(os.getenv("PG_POOL_MAX", "5"))
        self._pool: psycopg2.pool.ThreadedConnectionPool | None = None

    def _get_pool(self) -> psycopg2.pool.ThreadedConnectionPool:
        """Lazily create the connection pool on first use."""
        if self._pool is None or self._pool.closed:
            self._pool = psycopg2.pool.ThreadedConnectionPool(
                self._min_conn,
                self._max_conn,
                self._dsn,
            )
            logger.info(
                "PostgreSQL connection pool created (min=%d, max=%d).",
                self._min_conn,
                self._max_conn,
            )
        return self._pool

    @contextmanager
    def _conn(self) -> Generator[psycopg2.extensions.connection, None, None]:
        """Borrow a connection from the pool, auto-return on exit."""
        pool = self._get_pool()
        conn = pool.getconn()
        try:
            yield conn
        finally:
            pool.putconn(conn)

    def initialize(self) -> None:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(_CREATE_TABLE_SQL)
                cur.execute(_CREATE_INDEX_SQL)
                cur.execute(_CREATE_USER_SYMBOLS_SQL)
                cur.execute(_CREATE_USERS_SQL)
                for migration in _MIGRATIONS_SQL:
                    cur.execute(migration)
            conn.commit()

    def save(self, record: ReportRecord) -> int:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO reports
                        (symbol, verdict, summary, price_section, dividend_section,
                         movement_section, valuation_section, controversy_section, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        record.symbol,
                        record.verdict,
                        record.summary,
                        record.price_section,
                        record.dividend_section,
                        record.movement_section,
                        record.valuation_section,
                        record.controversy_section,
                        record.created_at or datetime.now(tz=UTC),
                    ),
                )
                row = cur.fetchone()
                record_id: int = row[0]  # type: ignore[index]
            conn.commit()
            record.id = record_id
            return record_id

    def get_by_id(self, record_id: int) -> Optional[ReportRecord]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute("SELECT * FROM reports WHERE id = %s", (record_id,))
                row = cur.fetchone()
            return self._row_to_record(row) if row else None

    def get_latest_by_symbol(self, symbol: str) -> Optional[ReportRecord]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(
                    "SELECT * FROM reports WHERE symbol = %s ORDER BY created_at DESC LIMIT 1",
                    (symbol.upper(),),
                )
                row = cur.fetchone()
            return self._row_to_record(row) if row else None

    def list_by_symbol(self, symbol: str, limit: int = 10) -> list[ReportRecord]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(
                    "SELECT * FROM reports WHERE symbol = %s ORDER BY created_at DESC LIMIT %s",
                    (symbol.upper(), limit),
                )
                rows = cur.fetchall()
            return [self._row_to_record(r) for r in rows]

    def list_recent_symbols(self, limit: int = 50) -> list[ReportRecord]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(
                    """
                    SELECT DISTINCT ON (symbol) *
                    FROM reports
                    ORDER BY symbol, created_at DESC
                    """,
                )
                all_rows = cur.fetchall()
            # Sort by created_at descending across symbols, then apply limit
            all_rows.sort(key=lambda r: r["created_at"], reverse=True)
            return [self._row_to_record(r) for r in all_rows[:limit]]

    # ------------------------------------------------------------------
    # Per-user symbol tracking
    # ------------------------------------------------------------------

    def add_user_symbol(self, user_id: str, symbol: str) -> None:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO user_symbols (user_id, symbol)
                    VALUES (%s, %s)
                    ON CONFLICT (user_id, symbol) DO NOTHING
                    """,
                    (user_id, symbol.upper()),
                )
            conn.commit()

    def list_user_symbols(
        self, user_id: str, limit: int = 50
    ) -> list[ReportRecord]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(
                    """
                    SELECT DISTINCT ON (r.symbol) r.*
                    FROM reports r
                    WHERE r.symbol IN (
                        SELECT symbol FROM user_symbols WHERE user_id = %s
                    )
                    ORDER BY r.symbol, r.created_at DESC
                    """,
                    (user_id,),
                )
                all_rows = cur.fetchall()
            all_rows.sort(key=lambda r: r["created_at"], reverse=True)
            return [self._row_to_record(r) for r in all_rows[:limit]]

    def close(self) -> None:
        """Close all pooled connections and release resources."""
        if self._pool and not self._pool.closed:
            self._pool.closeall()
            self._pool = None

    # ------------------------------------------------------------------
    # User persistence
    # ------------------------------------------------------------------

    def save_user(self, user: UserRecord) -> None:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO users (oid, name, email, provider, user_type, created_at, last_login_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (oid) DO UPDATE SET
                        name          = EXCLUDED.name,
                        email         = EXCLUDED.email,
                        provider      = EXCLUDED.provider,
                        last_login_at = EXCLUDED.last_login_at
                    """,
                    (
                        user.oid,
                        user.name,
                        user.email,
                        user.provider,
                        user.user_type,
                        user.created_at,
                        user.last_login_at or datetime.now(tz=UTC),
                    ),
                )
            conn.commit()

    def get_user(self, oid: str) -> Optional[UserRecord]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute("SELECT * FROM users WHERE oid = %s", (oid,))
                row = cur.fetchone()
            if row is None:
                return None
            return UserRecord(
                oid=row["oid"],
                name=row["name"],
                email=row["email"],
                provider=row["provider"],
                user_type=row["user_type"],
                created_at=row["created_at"],
                last_login_at=row["last_login_at"],
            )

    def get_user_by_email(self, email: str) -> Optional[UserRecord]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(
                    "SELECT * FROM users WHERE email = %s LIMIT 1", (email,)
                )
                row = cur.fetchone()
            if row is None:
                return None
            return UserRecord(
                oid=row["oid"],
                name=row["name"],
                email=row["email"],
                provider=row["provider"],
                user_type=row["user_type"],
                created_at=row["created_at"],
                last_login_at=row["last_login_at"],
            )

    @staticmethod
    def _row_to_record(row) -> ReportRecord:
        return ReportRecord(
            id=row["id"],
            symbol=row["symbol"],
            verdict=row["verdict"],
            summary=row["summary"],
            price_section=row["price_section"],
            dividend_section=row["dividend_section"],
            movement_section=row["movement_section"],
            valuation_section=row["valuation_section"],
            controversy_section=row["controversy_section"],
            created_at=row["created_at"],
        )
