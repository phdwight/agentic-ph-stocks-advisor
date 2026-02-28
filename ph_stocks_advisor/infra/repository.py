"""
Abstract repository interface for persisting analysis reports.

Dependency Inversion Principle: all consumers depend on this abstract
interface, never on a concrete database implementation.
"""

from __future__ import annotations

import abc
from datetime import UTC, datetime
from typing import Optional

from ph_stocks_advisor.data.models import FinalReport


class ReportRecord:
    """A persisted report with metadata."""

    def __init__(
        self,
        id: int | None,
        symbol: str,
        verdict: str,
        summary: str,
        price_section: str,
        dividend_section: str,
        movement_section: str,
        valuation_section: str,
        controversy_section: str,
        created_at: datetime | None = None,
    ) -> None:
        self.id = id
        self.symbol = symbol
        self.verdict = verdict
        self.summary = summary
        self.price_section = price_section
        self.dividend_section = dividend_section
        self.movement_section = movement_section
        self.valuation_section = valuation_section
        self.controversy_section = controversy_section
        self.created_at = created_at or datetime.now(tz=UTC)

    @classmethod
    def from_final_report(cls, report: FinalReport) -> "ReportRecord":
        return cls(
            id=None,
            symbol=report.symbol,
            verdict=report.verdict.value,
            summary=report.summary,
            price_section=report.price_section,
            dividend_section=report.dividend_section,
            movement_section=report.movement_section,
            valuation_section=report.valuation_section,
            controversy_section=report.controversy_section,
        )

    def __repr__(self) -> str:
        return (
            f"ReportRecord(id={self.id}, symbol={self.symbol!r}, "
            f"verdict={self.verdict!r}, created_at={self.created_at!r})"
        )


class AbstractReportRepository(abc.ABC):
    """
    Interface that all report repositories must implement.

    Follows the Interface Segregation Principle — only the operations
    callers actually need are declared here.
    """

    @abc.abstractmethod
    def initialize(self) -> None:
        """Create tables / schema if they don't exist."""

    @abc.abstractmethod
    def save(self, record: ReportRecord) -> int:
        """Persist a report record. Returns the generated ID."""

    @abc.abstractmethod
    def get_by_id(self, record_id: int) -> Optional[ReportRecord]:
        """Retrieve a single report by its ID."""

    @abc.abstractmethod
    def get_latest_by_symbol(self, symbol: str) -> Optional[ReportRecord]:
        """Return the most recent report for a given stock symbol."""

    @abc.abstractmethod
    def list_by_symbol(self, symbol: str, limit: int = 10) -> list[ReportRecord]:
        """Return recent reports for a symbol, newest first."""

    @abc.abstractmethod
    def list_recent_symbols(self, limit: int = 50) -> list[ReportRecord]:
        """Return the latest report for each distinct symbol, newest first."""

    # ------------------------------------------------------------------
    # Per-user symbol tracking
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def add_user_symbol(self, user_id: str, symbol: str) -> None:
        """Record that *user_id* has analysed *symbol*.

        Implementations must be idempotent — calling this twice for the
        same (user_id, symbol) pair must not raise or create duplicates.
        """

    @abc.abstractmethod
    def list_user_symbols(
        self, user_id: str, limit: int = 50
    ) -> list[ReportRecord]:
        """Return the latest report for each symbol the user has analysed.

        Behaves like ``list_recent_symbols`` but scoped to symbols the
        given user has previously requested.
        """

    @abc.abstractmethod
    def close(self) -> None:
        """Release database resources."""
