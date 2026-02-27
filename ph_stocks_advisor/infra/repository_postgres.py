"""
PostgreSQL implementation of the report repository.

Used in production environments.  Requires `psycopg2` (or `psycopg2-binary`).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Optional

import psycopg2  # type: ignore[import-untyped]
import psycopg2.extras  # type: ignore[import-untyped]

from ph_stocks_advisor.infra.repository import AbstractReportRepository, ReportRecord

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


class PostgresReportRepository(AbstractReportRepository):
    """PostgreSQL-backed repository — for production / multi-user use."""

    def __init__(self, dsn: str) -> None:
        """
        Args:
            dsn: PostgreSQL connection string, e.g.
                 ``"host=localhost dbname=ph_advisor user=app password=secret"``
                 or a full URI ``"postgresql://user:pass@host:5432/dbname"``.
        """
        self._dsn = dsn
        self._conn: psycopg2.extensions.connection | None = None

    def _get_conn(self) -> psycopg2.extensions.connection:
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(self._dsn)
        return self._conn

    def initialize(self) -> None:
        conn = self._get_conn()
        with conn.cursor() as cur:
            cur.execute(_CREATE_TABLE_SQL)
            cur.execute(_CREATE_INDEX_SQL)
        conn.commit()

    def save(self, record: ReportRecord) -> int:
        conn = self._get_conn()
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
        conn = self._get_conn()
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute("SELECT * FROM reports WHERE id = %s", (record_id,))
            row = cur.fetchone()
        return self._row_to_record(row) if row else None

    def get_latest_by_symbol(self, symbol: str) -> Optional[ReportRecord]:
        conn = self._get_conn()
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                "SELECT * FROM reports WHERE symbol = %s ORDER BY created_at DESC LIMIT 1",
                (symbol.upper(),),
            )
            row = cur.fetchone()
        return self._row_to_record(row) if row else None

    def list_by_symbol(self, symbol: str, limit: int = 10) -> list[ReportRecord]:
        conn = self._get_conn()
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                "SELECT * FROM reports WHERE symbol = %s ORDER BY created_at DESC LIMIT %s",
                (symbol.upper(), limit),
            )
            rows = cur.fetchall()
        return [self._row_to_record(r) for r in rows]

    def list_recent_symbols(self, limit: int = 50) -> list[ReportRecord]:
        conn = self._get_conn()
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

    def close(self) -> None:
        if self._conn and not self._conn.closed:
            self._conn.close()
            self._conn = None

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
