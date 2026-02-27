"""
SQLite implementation of the report repository.

Used as the default backend for local development.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Optional

from ph_stocks_advisor.infra.repository import AbstractReportRepository, ReportRecord

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

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

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
