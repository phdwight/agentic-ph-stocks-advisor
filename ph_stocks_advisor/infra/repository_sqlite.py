"""
SQLite implementation of the report repository.

Used as the default backend for local development.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Optional

from ph_stocks_advisor.infra.repository import AbstractReportRepository, ReportRecord, UserRecord

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS reports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT    NOT NULL,
    verdict         TEXT    NOT NULL,
    summary         TEXT    NOT NULL,
    price_section   TEXT    NOT NULL DEFAULT '',
    dividend_section TEXT   NOT NULL DEFAULT '',
    movement_section TEXT   NOT NULL DEFAULT '',
    valuation_section TEXT  NOT NULL DEFAULT '',
    controversy_section TEXT NOT NULL DEFAULT '',
    created_at      TEXT    NOT NULL
);
"""

_CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_reports_symbol_created
ON reports (symbol, created_at DESC);
"""

_CREATE_USER_SYMBOLS_SQL = """
CREATE TABLE IF NOT EXISTS user_symbols (
    user_id    TEXT NOT NULL,
    symbol     TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, symbol)
);
"""

_CREATE_USERS_SQL = """
CREATE TABLE IF NOT EXISTS users (
    oid           TEXT PRIMARY KEY,
    name          TEXT NOT NULL DEFAULT '',
    email         TEXT NOT NULL DEFAULT '',
    provider      TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    last_login_at TEXT NOT NULL
);
"""


class SQLiteReportRepository(AbstractReportRepository):
    """SQLite-backed repository — great for dev / single-user use."""

    def __init__(self, db_path: str = "reports.db") -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def initialize(self) -> None:
        conn = self._get_conn()
        conn.execute(_CREATE_TABLE_SQL)
        conn.execute(_CREATE_INDEX_SQL)
        conn.execute(_CREATE_USER_SYMBOLS_SQL)
        conn.execute(_CREATE_USERS_SQL)
        conn.commit()

    def save(self, record: ReportRecord) -> int:
        conn = self._get_conn()
        cursor = conn.execute(
            """
            INSERT INTO reports
                (symbol, verdict, summary, price_section, dividend_section,
                 movement_section, valuation_section, controversy_section, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                record.created_at.isoformat() if record.created_at else datetime.now(tz=UTC).isoformat(),
            ),
        )
        conn.commit()
        record.id = cursor.lastrowid
        return cursor.lastrowid  # type: ignore[return-value]

    def get_by_id(self, record_id: int) -> Optional[ReportRecord]:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM reports WHERE id = ?", (record_id,)).fetchone()
        return self._row_to_record(row) if row else None

    def get_latest_by_symbol(self, symbol: str) -> Optional[ReportRecord]:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM reports WHERE symbol = ? ORDER BY created_at DESC LIMIT 1",
            (symbol.upper(),),
        ).fetchone()
        return self._row_to_record(row) if row else None

    def list_by_symbol(self, symbol: str, limit: int = 10) -> list[ReportRecord]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM reports WHERE symbol = ? ORDER BY created_at DESC LIMIT ?",
            (symbol.upper(), limit),
        ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def list_recent_symbols(self, limit: int = 50) -> list[ReportRecord]:
        conn = self._get_conn()
        rows = conn.execute(
            """
            SELECT r.* FROM reports r
            INNER JOIN (
                SELECT symbol, MAX(created_at) AS max_ca
                FROM reports GROUP BY symbol
            ) g ON r.symbol = g.symbol AND r.created_at = g.max_ca
            ORDER BY r.created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [self._row_to_record(r) for r in rows]

    # ------------------------------------------------------------------
    # Per-user symbol tracking
    # ------------------------------------------------------------------

    def add_user_symbol(self, user_id: str, symbol: str) -> None:
        conn = self._get_conn()
        conn.execute(
            """
            INSERT OR IGNORE INTO user_symbols (user_id, symbol, created_at)
            VALUES (?, ?, ?)
            """,
            (user_id, symbol.upper(), datetime.now(tz=UTC).isoformat()),
        )
        conn.commit()

    def list_user_symbols(
        self, user_id: str, limit: int = 50
    ) -> list[ReportRecord]:
        conn = self._get_conn()
        rows = conn.execute(
            """
            SELECT r.* FROM reports r
            INNER JOIN (
                SELECT symbol, MAX(created_at) AS max_ca
                FROM reports GROUP BY symbol
            ) g ON r.symbol = g.symbol AND r.created_at = g.max_ca
            WHERE r.symbol IN (
                SELECT symbol FROM user_symbols WHERE user_id = ?
            )
            ORDER BY r.created_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # User persistence
    # ------------------------------------------------------------------

    def save_user(self, user: UserRecord) -> None:
        conn = self._get_conn()
        conn.execute(
            """
            INSERT INTO users (oid, name, email, provider, created_at, last_login_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(oid) DO UPDATE SET
                name          = excluded.name,
                email         = excluded.email,
                provider      = excluded.provider,
                last_login_at = excluded.last_login_at
            """,
            (
                user.oid,
                user.name,
                user.email,
                user.provider,
                user.created_at.isoformat(),
                user.last_login_at.isoformat() if user.last_login_at else datetime.now(tz=UTC).isoformat(),
            ),
        )
        conn.commit()

    def get_user(self, oid: str) -> Optional[UserRecord]:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM users WHERE oid = ?", (oid,)).fetchone()
        if row is None:
            return None
        return UserRecord(
            oid=row["oid"],
            name=row["name"],
            email=row["email"],
            provider=row["provider"],
            created_at=datetime.fromisoformat(row["created_at"]),
            last_login_at=datetime.fromisoformat(row["last_login_at"]),
        )

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> ReportRecord:
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
            created_at=datetime.fromisoformat(row["created_at"]),
        )
