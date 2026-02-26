"""
Data models for the Philippine Stocks Advisor.

All Pydantic models representing domain data, agent analysis results,
and the shared graph state used across the LangGraph workflow.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class Verdict(str, Enum):
    """Final investment verdict."""

    BUY = "BUY"
    NOT_BUY = "NOT BUY"


class TrendDirection(str, Enum):
    UPTREND = "uptrend"
    DOWNTREND = "downtrend"
    SIDEWAYS = "sideways"


# ---------------------------------------------------------------------------
# Domain value objects
# ---------------------------------------------------------------------------

class StockPrice(BaseModel):
    """Current and historical price information."""

    symbol: str
    current_price: float
    currency: str = "PHP"
    fifty_two_week_high: float = 0.0
    fifty_two_week_low: float = 0.0
    previous_close: float = 0.0


class DividendInfo(BaseModel):
    """Dividend-related metrics."""

    symbol: str
    dividend_rate: float = 0.0
    dividend_yield: float = 0.0
    payout_ratio: float = 0.0
    ex_dividend_date: Optional[str] = None
    five_year_avg_yield: float = 0.0


class PriceMovement(BaseModel):
    """One-year price movement summary."""

    symbol: str
    year_start_price: float = 0.0
    year_end_price: float = 0.0
    year_change_pct: float = 0.0
    max_price: float = 0.0
    min_price: float = 0.0
    volatility: float = 0.0
    trend: TrendDirection = TrendDirection.SIDEWAYS
    monthly_prices: list[float] = Field(default_factory=list)


class FairValueEstimate(BaseModel):
    """Fair value estimation data."""

    symbol: str
    current_price: float = 0.0
    book_value: float = 0.0
    pe_ratio: float = 0.0
    pb_ratio: float = 0.0
    peg_ratio: float = 0.0
    forward_pe: float = 0.0
    estimated_fair_value: float = 0.0
    discount_pct: float = 0.0  # positive = undervalued


class ControversyInfo(BaseModel):
    """Risk and controversy data."""

    symbol: str
    sudden_spikes: list[str] = Field(default_factory=list)
    risk_factors: list[str] = Field(default_factory=list)
    recent_news_summary: str = ""


# ---------------------------------------------------------------------------
# Agent analysis results
# ---------------------------------------------------------------------------

class PriceAnalysis(BaseModel):
    """Output from the Price Analysis Agent."""

    data: StockPrice
    analysis: str = ""


class DividendAnalysis(BaseModel):
    """Output from the Dividend Analysis Agent."""

    data: DividendInfo
    analysis: str = ""


class MovementAnalysis(BaseModel):
    """Output from the Price Movement Agent."""

    data: PriceMovement
    analysis: str = ""


class ValuationAnalysis(BaseModel):
    """Output from the Valuation Agent."""

    data: FairValueEstimate
    analysis: str = ""


class ControversyAnalysis(BaseModel):
    """Output from the Controversy/Risk Agent."""

    data: ControversyInfo
    analysis: str = ""


class FinalReport(BaseModel):
    """The consolidated investment report."""

    symbol: str
    verdict: Verdict
    summary: str
    price_section: str = ""
    dividend_section: str = ""
    movement_section: str = ""
    valuation_section: str = ""
    controversy_section: str = ""


# ---------------------------------------------------------------------------
# LangGraph shared state
# ---------------------------------------------------------------------------

class AdvisorState(BaseModel):
    """
    Shared state flowing through the LangGraph workflow.

    Each agent reads `symbol` and writes its own analysis field.
    The consolidator reads all analysis fields and writes `final_report`.
    """

    symbol: str = ""
    price_analysis: Optional[PriceAnalysis] = None
    dividend_analysis: Optional[DividendAnalysis] = None
    movement_analysis: Optional[MovementAnalysis] = None
    valuation_analysis: Optional[ValuationAnalysis] = None
    controversy_analysis: Optional[ControversyAnalysis] = None
    final_report: Optional[FinalReport] = None
