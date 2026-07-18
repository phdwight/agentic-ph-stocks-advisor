"""
PostgreSQL implementation of the report repository.

Used in production environments.  Requires `psycopg2` (or `psycopg2-binary`).

Uses a **thread-safe connection pool** (``psycopg2.pool.ThreadedConnectionPool``)
so multiple Gunicorn threads / Celery workers share a bounded set of
database connections instead of opening one per request.
"""

from __future__ import annotations

import contextlib
import logging
import os
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime

import psycopg2  # type: ignore[import-untyped]
import psycopg2.extras  # type: ignore[import-untyped]
import psycopg2.pool  # type: ignore[import-untyped]

from ph_stocks_advisor.infra.repository import (
    AbstractReportRepository,
    HoldingRecord,
    PortfolioReportRecord,
    ReportRecord,
    UserRecord,
    WebAuthnCredentialRecord,
)

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
    sentiment_section   TEXT        NOT NULL DEFAULT '',
    score               INTEGER,
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

_CREATE_WEBAUTHN_SQL = """
CREATE TABLE IF NOT EXISTS webauthn_credentials (
    credential_id TEXT PRIMARY KEY,
    user_oid      VARCHAR(320) NOT NULL,
    public_key    TEXT         NOT NULL,
    sign_count    BIGINT       NOT NULL DEFAULT 0,
    transports    TEXT         NOT NULL DEFAULT '',
    aaguid        TEXT,
    nickname      TEXT,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    last_used_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_webauthn_user ON webauthn_credentials(user_oid);
"""

# ── Schema migrations (idempotent) ──────────────────────────────────────────
_MIGRATIONS_SQL = [
    # Verdict score (0-100 avoid->buy scale) for reports created pre-scoring
    """
    ALTER TABLE reports
        ADD COLUMN IF NOT EXISTS score INTEGER;
    """,
    # Added in v2 — user_type column for NORMAL(0)/ELEVATED(1) privileges
    """
    ALTER TABLE users
        ADD COLUMN IF NOT EXISTS user_type INTEGER NOT NULL DEFAULT 0;
    """,
    # Added in v3 — holdings table for elevated-user stock positions
    """
    CREATE TABLE IF NOT EXISTS holdings (
        user_id    VARCHAR(320) NOT NULL,
        symbol     VARCHAR(20)  NOT NULL,
        shares     DOUBLE PRECISION NOT NULL,
        avg_cost   DOUBLE PRECISION NOT NULL,
        updated_at TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
        PRIMARY KEY (user_id, symbol)
    );
    """,
    # Added in v3 — portfolio_reports for user-private portfolio-aware analyses
    """
    CREATE TABLE IF NOT EXISTS portfolio_reports (
        id              SERIAL PRIMARY KEY,
        user_id         VARCHAR(320) NOT NULL,
        symbol          VARCHAR(20)  NOT NULL,
        shares          DOUBLE PRECISION NOT NULL,
        avg_cost        DOUBLE PRECISION NOT NULL,
        analysis        TEXT         NOT NULL,
        base_report_id  INTEGER,
        created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_portfolio_reports_user_symbol
    ON portfolio_reports (user_id, symbol, created_at DESC);
    """,
    # Added in v4 — sentiment_section for global-events / macro-risk analysis
    """
    ALTER TABLE reports
        ADD COLUMN IF NOT EXISTS sentiment_section TEXT NOT NULL DEFAULT '';
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

    def _is_conn_alive(self, conn: psycopg2.extensions.connection) -> bool:
        """Cheaply test whether a pooled connection is still usable.

        PostgreSQL servers (and intermediate proxies) may close idle
        connections after a timeout, causing the next operation to raise
        ``OperationalError('server closed the connection unexpectedly')``.
        We probe with ``SELECT 1`` and return ``False`` for any failure
        so the caller can recycle the connection.
        """
        if conn.closed:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
            # Reset any leftover transaction state from the probe so the
            # caller starts cleanly.
            conn.rollback()
            return True
        except psycopg2.Error:
            return False

    @contextmanager
    def _conn(self) -> Generator[psycopg2.extensions.connection]:
        """Borrow a connection from the pool, auto-return on exit.

        Validates the connection before yielding it and recycles dead
        connections so callers never see a stale socket.  If the caller
        raises an :class:`OperationalError` (indicating the connection
        died mid-request), the connection is closed instead of being
        returned to the pool.
        """
        pool = self._get_pool()

        # Borrow a live connection, replacing any dead ones we find.
        # Bound the retry loop to ``max_conn`` so we cannot spin forever
        # in the unlikely event the entire pool is poisoned.
        conn: psycopg2.extensions.connection | None = None
        for _ in range(max(self._max_conn, 1)):
            candidate = pool.getconn()
            if self._is_conn_alive(candidate):
                conn = candidate
                break
            logger.warning(
                "Discarding dead PostgreSQL connection from pool; the server likely closed it.",
            )
            try:
                pool.putconn(candidate, close=True)
            except Exception:
                logger.debug("Failed returning dead connection to pool.", exc_info=True)
        if conn is None:
            # Last resort: try one more checkout without the liveness
            # probe; the caller's error handling will surface a clear
            # exception if it is also dead.
            conn = pool.getconn()
        assert conn is not None  # noqa: S101 - narrow type for static checkers

        broken = False
        try:
            yield conn
        except psycopg2.OperationalError, psycopg2.InterfaceError:
            # Connection died mid-request (server closed socket, network
            # blip, or it was already closed).  Don't return it to the pool.
            broken = True
            raise
        except psycopg2.Error:
            # Roll back so the next borrower doesn't inherit an aborted
            # transaction; the connection itself is still usable.
            with contextlib.suppress(psycopg2.Error):
                conn.rollback()
            raise
        finally:
            try:
                pool.putconn(conn, close=bool(broken or conn.closed))
            except Exception:
                logger.debug("Failed returning connection to pool.", exc_info=True)

    def initialize(self) -> None:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(_CREATE_TABLE_SQL)
                cur.execute(_CREATE_INDEX_SQL)
                cur.execute(_CREATE_USER_SYMBOLS_SQL)
                cur.execute(_CREATE_USERS_SQL)
                cur.execute(_CREATE_WEBAUTHN_SQL)
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
                         movement_section, valuation_section, controversy_section,
                         sentiment_section, score, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                        record.sentiment_section,
                        record.score,
                        record.created_at or datetime.now(tz=UTC),
                    ),
                )
                row = cur.fetchone()
                record_id: int = row[0]  # type: ignore[index]
            conn.commit()
            record.id = record_id
            return record_id

    def get_by_id(self, record_id: int) -> ReportRecord | None:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute("SELECT * FROM reports WHERE id = %s", (record_id,))
                row = cur.fetchone()
            return self._row_to_record(row) if row else None

    def get_latest_by_symbol(self, symbol: str) -> ReportRecord | None:
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

    def list_user_symbols(self, user_id: str, limit: int = 50) -> list[ReportRecord]:
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

    def get_user(self, oid: str) -> UserRecord | None:
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

    def get_user_by_email(self, email: str) -> UserRecord | None:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute("SELECT * FROM users WHERE email = %s LIMIT 1", (email,))
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

    # ------------------------------------------------------------------
    # WebAuthn / passkey credentials
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_credential(row) -> WebAuthnCredentialRecord:
        return WebAuthnCredentialRecord(
            credential_id=row["credential_id"],
            user_oid=row["user_oid"],
            public_key=row["public_key"],
            sign_count=row["sign_count"],
            transports=row["transports"].split(",") if row["transports"] else [],
            aaguid=row["aaguid"],
            nickname=row["nickname"],
            created_at=row["created_at"],
            last_used_at=row["last_used_at"],
        )

    def add_webauthn_credential(self, cred: WebAuthnCredentialRecord) -> None:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO webauthn_credentials
                        (credential_id, user_oid, public_key, sign_count, transports,
                         aaguid, nickname, created_at, last_used_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        cred.credential_id,
                        cred.user_oid,
                        cred.public_key,
                        cred.sign_count,
                        ",".join(cred.transports),
                        cred.aaguid,
                        cred.nickname,
                        cred.created_at,
                        cred.last_used_at,
                    ),
                )
            conn.commit()

    def get_webauthn_credential(self, credential_id: str) -> WebAuthnCredentialRecord | None:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(
                    "SELECT * FROM webauthn_credentials WHERE credential_id = %s",
                    (credential_id,),
                )
                row = cur.fetchone()
            return self._row_to_credential(row) if row else None

    def list_webauthn_credentials(self, user_oid: str) -> list[WebAuthnCredentialRecord]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(
                    "SELECT * FROM webauthn_credentials WHERE user_oid = %s ORDER BY created_at DESC",
                    (user_oid,),
                )
                rows = cur.fetchall()
            return [self._row_to_credential(r) for r in rows]

    def update_webauthn_sign_count(self, credential_id: str, sign_count: int) -> None:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE webauthn_credentials SET sign_count = %s, last_used_at = %s WHERE credential_id = %s",
                    (sign_count, datetime.now(tz=UTC), credential_id),
                )
            conn.commit()

    def delete_webauthn_credential(self, credential_id: str, user_oid: str) -> None:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM webauthn_credentials WHERE credential_id = %s AND user_oid = %s",
                    (credential_id, user_oid),
                )
            conn.commit()

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
            sentiment_section=row.get("sentiment_section", "") if hasattr(row, "get") else "",
            score=row.get("score") if hasattr(row, "get") else None,
            created_at=row["created_at"],
        )

    # ------------------------------------------------------------------
    # Holdings
    # ------------------------------------------------------------------

    def save_holding(self, holding: HoldingRecord) -> None:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO holdings (user_id, symbol, shares, avg_cost, updated_at)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (user_id, symbol) DO UPDATE SET
                        shares     = EXCLUDED.shares,
                        avg_cost   = EXCLUDED.avg_cost,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (
                        holding.user_id,
                        holding.symbol.upper(),
                        holding.shares,
                        holding.avg_cost,
                        holding.updated_at or datetime.now(tz=UTC),
                    ),
                )
            conn.commit()

    def get_holding(self, user_id: str, symbol: str) -> HoldingRecord | None:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(
                    "SELECT * FROM holdings WHERE user_id = %s AND symbol = %s",
                    (user_id, symbol.upper()),
                )
                row = cur.fetchone()
            if row is None:
                return None
            return HoldingRecord(
                user_id=row["user_id"],
                symbol=row["symbol"],
                shares=row["shares"],
                avg_cost=row["avg_cost"],
                updated_at=row["updated_at"],
            )

    def delete_holding(self, user_id: str, symbol: str) -> None:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM holdings WHERE user_id = %s AND symbol = %s",
                    (user_id, symbol.upper()),
                )
            conn.commit()

    def list_holdings(self, user_id: str) -> list[HoldingRecord]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(
                    "SELECT * FROM holdings WHERE user_id = %s ORDER BY symbol",
                    (user_id,),
                )
                rows = cur.fetchall()
            return [
                HoldingRecord(
                    user_id=r["user_id"],
                    symbol=r["symbol"],
                    shares=r["shares"],
                    avg_cost=r["avg_cost"],
                    updated_at=r["updated_at"],
                )
                for r in rows
            ]

    # ------------------------------------------------------------------
    # Portfolio reports
    # ------------------------------------------------------------------

    def save_portfolio_report(self, record: PortfolioReportRecord) -> int:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO portfolio_reports
                        (user_id, symbol, shares, avg_cost, analysis,
                         base_report_id, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        record.user_id,
                        record.symbol.upper(),
                        record.shares,
                        record.avg_cost,
                        record.analysis,
                        record.base_report_id,
                        record.created_at or datetime.now(tz=UTC),
                    ),
                )
                row = cur.fetchone()
                record_id: int = row[0]  # type: ignore[index]
            conn.commit()
            record.id = record_id
            return record_id

    def get_portfolio_report(
        self,
        user_id: str,
        symbol: str,
    ) -> PortfolioReportRecord | None:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(
                    """
                    SELECT * FROM portfolio_reports
                    WHERE user_id = %s AND symbol = %s
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (user_id, symbol.upper()),
                )
                row = cur.fetchone()
            if row is None:
                return None
            return PortfolioReportRecord(
                id=row["id"],
                user_id=row["user_id"],
                symbol=row["symbol"],
                shares=row["shares"],
                avg_cost=row["avg_cost"],
                analysis=row["analysis"],
                base_report_id=row["base_report_id"],
                created_at=row["created_at"],
            )
