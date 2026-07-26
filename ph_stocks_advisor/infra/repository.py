"""
Abstract repository interface for persisting analysis reports.

Dependency Inversion Principle: all consumers depend on this abstract
interface, never on a concrete database implementation.
"""

from __future__ import annotations

import abc
from datetime import UTC, datetime
from enum import IntEnum

from ph_stocks_advisor.data.models import FinalReport


class UserType(IntEnum):
    """User privilege level.

    NORMAL (0) — default; subject to daily analysis limits and
    cached-report deduplication.
    ELEVATED (1) — exempt from the daily limit and allowed to
    re-analyse stocks that already have a cached report.
    """

    NORMAL = 0
    ELEVATED = 1


class UserRecord:
    """A persisted user profile from OAuth sign-in."""

    def __init__(
        self,
        oid: str,
        name: str,
        email: str,
        provider: str,
        created_at: datetime | None = None,
        last_login_at: datetime | None = None,
        user_type: int = UserType.NORMAL,
        disclaimer_version: str | None = None,
        disclaimer_accepted_at: datetime | None = None,
    ) -> None:
        self.oid = oid
        self.name = name
        self.email = email
        self.provider = provider
        self.created_at = created_at or datetime.now(tz=UTC)
        self.last_login_at = last_login_at or datetime.now(tz=UTC)
        self.user_type = user_type
        # Proof of consent captured at sign-up: which version of the
        # Disclaimer & Terms the user accepted, and when. ``None`` for
        # accounts created before consent was recorded.
        self.disclaimer_version = disclaimer_version
        self.disclaimer_accepted_at = disclaimer_accepted_at

    @property
    def is_elevated(self) -> bool:
        """Return ``True`` if this user has elevated privileges."""
        return self.user_type == UserType.ELEVATED

    def __repr__(self) -> str:
        return (
            f"UserRecord(oid={self.oid!r}, email={self.email!r}, "
            f"provider={self.provider!r}, user_type={self.user_type})"
        )


class WebAuthnCredentialRecord:
    """A registered passkey (WebAuthn credential) bound to a user.

    Binary fields (``credential_id``, ``public_key``) are stored as
    base64url-encoded strings so the same schema works on SQLite and
    Postgres without blob-handling differences; the auth layer encodes
    and decodes at its boundary.
    """

    def __init__(
        self,
        credential_id: str,
        user_oid: str,
        public_key: str,
        sign_count: int = 0,
        transports: list[str] | None = None,
        aaguid: str | None = None,
        nickname: str | None = None,
        created_at: datetime | None = None,
        last_used_at: datetime | None = None,
    ) -> None:
        self.credential_id = credential_id  # base64url
        self.user_oid = user_oid
        self.public_key = public_key  # base64url of the COSE key bytes
        self.sign_count = sign_count
        self.transports = transports or []
        self.aaguid = aaguid
        self.nickname = nickname
        self.created_at = created_at or datetime.now(tz=UTC)
        self.last_used_at = last_used_at

    def __repr__(self) -> str:
        return (
            f"WebAuthnCredentialRecord(user_oid={self.user_oid!r}, "
            f"nickname={self.nickname!r}, sign_count={self.sign_count})"
        )


class HoldingRecord:
    """A user's stock holding (shares held + average cost)."""

    def __init__(
        self,
        user_id: str,
        symbol: str,
        shares: float,
        avg_cost: float,
        updated_at: datetime | None = None,
    ) -> None:
        self.user_id = user_id
        self.symbol = symbol
        self.shares = shares
        self.avg_cost = avg_cost
        self.updated_at = updated_at or datetime.now(tz=UTC)

    @property
    def total_cost(self) -> float:
        """Total capital invested."""
        return self.shares * self.avg_cost

    def __repr__(self) -> str:
        return (
            f"HoldingRecord(user_id={self.user_id!r}, symbol={self.symbol!r}, "
            f"shares={self.shares}, avg_cost={self.avg_cost})"
        )


class PortfolioReportRecord:
    """A personalised portfolio-aware report visible only to its owner."""

    def __init__(
        self,
        id: int | None,
        user_id: str,
        symbol: str,
        shares: float,
        avg_cost: float,
        analysis: str,
        base_report_id: int | None = None,
        created_at: datetime | None = None,
    ) -> None:
        self.id = id
        self.user_id = user_id
        self.symbol = symbol
        self.shares = shares
        self.avg_cost = avg_cost
        self.analysis = analysis
        self.base_report_id = base_report_id
        self.created_at = created_at or datetime.now(tz=UTC)

    def __repr__(self) -> str:
        return f"PortfolioReportRecord(id={self.id}, user_id={self.user_id!r}, symbol={self.symbol!r})"


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
        sentiment_section: str = "",
        created_at: datetime | None = None,
        score: int | None = None,
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
        self.sentiment_section = sentiment_section
        self.created_at = created_at or datetime.now(tz=UTC)
        # 0–100 avoid→buy verdict score; None for pre-scoring legacy rows.
        self.score = score

    @classmethod
    def from_final_report(cls, report: FinalReport) -> ReportRecord:
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
            sentiment_section=report.sentiment_section,
            score=report.score,
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
    def get_by_id(self, record_id: int) -> ReportRecord | None:
        """Retrieve a single report by its ID."""

    @abc.abstractmethod
    def get_latest_by_symbol(self, symbol: str) -> ReportRecord | None:
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
    def list_user_symbols(self, user_id: str, limit: int = 50) -> list[ReportRecord]:
        """Return the latest report for each symbol the user has analysed.

        Behaves like ``list_recent_symbols`` but scoped to symbols the
        given user has previously requested.
        """

    # ------------------------------------------------------------------
    # User persistence
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def save_user(self, user: UserRecord) -> None:
        """Insert or update a user record.

        Implementations must be idempotent — if a user with the same
        ``oid`` already exists, update ``name``, ``email``, ``provider``,
        and ``last_login_at``.
        """

    @abc.abstractmethod
    def get_user(self, oid: str) -> UserRecord | None:
        """Retrieve a user by their unique ``oid``, or ``None``."""

    @abc.abstractmethod
    def get_user_by_email(self, email: str) -> UserRecord | None:
        """Retrieve a user by their email address, or ``None``."""

    # ------------------------------------------------------------------
    # WebAuthn / passkey credentials
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def add_webauthn_credential(self, cred: WebAuthnCredentialRecord) -> None:
        """Persist a newly-registered passkey."""

    @abc.abstractmethod
    def get_webauthn_credential(self, credential_id: str) -> WebAuthnCredentialRecord | None:
        """Look up a passkey by its base64url credential id, or ``None``."""

    @abc.abstractmethod
    def list_webauthn_credentials(self, user_oid: str) -> list[WebAuthnCredentialRecord]:
        """Return all passkeys registered to a user (newest first)."""

    @abc.abstractmethod
    def update_webauthn_sign_count(self, credential_id: str, sign_count: int) -> None:
        """Update a passkey's signature counter and last-used timestamp after a login."""

    @abc.abstractmethod
    def delete_webauthn_credential(self, credential_id: str, user_oid: str) -> None:
        """Remove a passkey. Scoped to ``user_oid`` so a user can only delete their own."""

    # ------------------------------------------------------------------
    # Holdings
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def save_holding(self, holding: HoldingRecord) -> None:
        """Insert or update a stock holding for a user.

        Implementations must be idempotent — if a holding for the same
        (user_id, symbol) already exists, update shares, avg_cost, and
        updated_at.
        """

    @abc.abstractmethod
    def get_holding(self, user_id: str, symbol: str) -> HoldingRecord | None:
        """Retrieve a user's holding for a specific symbol, or ``None``."""

    @abc.abstractmethod
    def delete_holding(self, user_id: str, symbol: str) -> None:
        """Remove a holding record.  No-op if it does not exist."""

    @abc.abstractmethod
    def list_holdings(self, user_id: str) -> list[HoldingRecord]:
        """Return all holdings for a user."""

    # ------------------------------------------------------------------
    # Portfolio reports (user-private)
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def save_portfolio_report(self, record: PortfolioReportRecord) -> int:
        """Persist a portfolio-aware report.  Returns the generated ID."""

    @abc.abstractmethod
    def get_portfolio_report(
        self,
        user_id: str,
        symbol: str,
    ) -> PortfolioReportRecord | None:
        """Return the latest portfolio report for a user + symbol."""

    @abc.abstractmethod
    def close(self) -> None:
        """Release database resources."""
