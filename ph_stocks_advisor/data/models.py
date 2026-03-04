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
    price_catalysts: list[str] = Field(
        default_factory=list,
        description="Detected factors likely driving the current price (e.g. dividend announcement, REIT yield play).",
    )


class DividendAnnouncement(BaseModel):
    """A single dividend announcement from PSE EDGE company page.

    Stores the key dates and rate that investors need to act on:
    the ex-dividend date (last day to buy to receive the dividend),
    the dividend rate per share, and the payment date.
    """

    security_type: str = Field(
        default="COMMON",
        description="Type of security, e.g. COMMON or PREFERRED.",
    )
    dividend_type: str = Field(
        default="Cash",
        description="Type of dividend, e.g. Cash or Stock.",
    )
    dividend_rate: str = Field(
        description="Dividend amount per share, e.g. 'Php0.62'.",
    )
    ex_date: str = Field(
        description="Ex-dividend date — last trading day to buy and still receive the dividend.",
    )
    record_date: str = Field(
        default="",
        description="Record date for the dividend.",
    )
    payment_date: str = Field(
        description="Date when the dividend is paid out.",
    )
    circular_number: str = Field(
        default="",
        description="PSE circular reference number, e.g. 'C01040-2026'.",
    )

    def to_summary(self) -> str:
        """Human-readable one-line summary."""
        return (
            f"{self.dividend_rate}/share, "
            f"ex-date {self.ex_date}, "
            f"payment {self.payment_date}"
        )


class DividendInfo(BaseModel):
    """Dividend-related metrics.

    Fields are grouped into three logical sections:

    1. **Core metrics** — basic dividend figures from market data.
    2. **Financial enrichment** — trend data from DragonFi financials used
       by the LLM to assess sustainability.
    3. **External context** — web-sourced news and computed notes.
    """

    symbol: str

    # -- Core dividend metrics -------------------------------------------------
    dividend_rate: float = 0.0
    dividend_yield: float = 0.0
    payout_ratio: float = 0.0
    ex_dividend_date: Optional[str] = None
    five_year_avg_yield: float = 0.0
    is_reit: bool = False
    annual_dividend_per_share: float = 0.0

    # -- Financial enrichment (DragonFi annual income statements) --------------
    net_income_trend: dict[str, float] = Field(
        default_factory=dict,
        description="Annual net income (PHP) keyed by year, e.g. {'2022': 2.89e9}",
    )
    revenue_trend: dict[str, float] = Field(
        default_factory=dict,
        description="Annual revenue (PHP) keyed by year",
    )
    free_cash_flow_trend: dict[str, float] = Field(
        default_factory=dict,
        description="Annual free cash flow (PHP) keyed by year",
    )

    # -- External context (computed notes + web news) --------------------------
    dividend_sustainability_note: str = ""
    recent_dividend_news: str = Field(
        default="",
        description="Recent dividend-related web news from Tavily search.",
    )
    recent_declared_dividends: str = Field(
        default="",
        description=(
            "Recent cash-dividend declarations scraped from PSE EDGE "
            "(SEC Form 6-1). Contains amount per share, ex-date, "
            "record date, and payment date."
        ),
    )
    dividend_announcements: list[DividendAnnouncement] = Field(
        default_factory=list,
        description=(
            "Structured list of recent dividend announcements from the "
            "PSE EDGE company dividends page. Each entry contains the "
            "ex-date, dividend rate per share, and payment date."
        ),
    )


class PriceMovement(BaseModel):
    """One-year price movement summary.

    Fields are grouped into three logical sections:

    1. **Core statistics** — annual return metrics, volatility, drawdown.
    2. **Trend & catalysts** — classified direction and detected drivers.
    3. **Technical context** — candlestick patterns, TradingView performance,
       and recent web news that contextualise the movement.
    """

    symbol: str

    # -- Core statistics -------------------------------------------------------
    year_start_price: float = 0.0
    year_end_price: float = 0.0
    year_change_pct: float = 0.0
    max_price: float = 0.0
    min_price: float = 0.0
    volatility: float = 0.0
    max_drawdown_pct: float = Field(
        default=0.0,
        description="Largest peak-to-trough decline (%) during the year. "
        "A value like -30.0 means the stock fell 30% from its high.",
    )
    monthly_prices: list[float] = Field(default_factory=list)

    # -- Trend & catalysts -----------------------------------------------------
    trend: TrendDirection = TrendDirection.SIDEWAYS
    price_catalysts: list[str] = Field(
        default_factory=list,
        description="Detected factors likely driving recent price movement.",
    )

    # -- Technical context (candlestick, TradingView, web news) ----------------
    candlestick_patterns: str = Field(
        default="",
        description="Notable candlestick chart patterns detected from OHLCV data "
        "(large candles, gaps, volume spikes, selling/buying pressure).",
    )
    performance_summary: str = Field(
        default="",
        description="Multi-period performance summary from TradingView "
        "(1-week, 1-month, 3-month, 6-month, 1-year, YTD % change + volatility).",
    )
    web_news: str = Field(
        default="",
        description="Recent web news about the stock from Tavily search.",
    )


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
    web_news: str = Field(
        default="",
        description="Recent general & controversy news from Tavily web search.",
    )


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


class ConsolidationResponse(BaseModel):
    """Structured LLM output from the consolidator agent.

    Used with ``BaseChatModel.with_structured_output()`` so the verdict
    is returned as a typed enum — no regex parsing required.
    """

    verdict: Verdict = Field(
        description="Final investment verdict. Must be exactly BUY or NOT BUY.",
    )
    justification: str = Field(
        description="One-sentence justification for the verdict.",
    )
    summary: str = Field(
        description=(
            "The full investment report in markdown format, including "
            "executive summary, bullet-pointed sections for each analysis "
            "area, and the verdict line."
        ),
    )


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
