"""
Shared test fixtures and helpers.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage

from ph_stocks_advisor.data.models import (
    AdvisorState,
    ControversyAnalysis,
    ControversyInfo,
    DividendAnalysis,
    DividendInfo,
    FairValueEstimate,
    FinalReport,
    MovementAnalysis,
    PriceAnalysis,
    PriceMovement,
    StockPrice,
    TrendDirection,
    ValuationAnalysis,
    Verdict,
)


# ---------------------------------------------------------------------------
# Mock LLM that returns canned responses
# ---------------------------------------------------------------------------


def make_mock_llm(response_text: str = "Mock analysis.") -> MagicMock:
    """Return a MagicMock that behaves like a BaseChatModel.

    The mock does NOT support ``with_structured_output`` — calling it
    raises ``NotImplementedError`` so the consolidator falls back to
    regex-based verdict extraction.
    """
    llm = MagicMock()
    llm.invoke.return_value = AIMessage(content=response_text)
    llm.with_structured_output.side_effect = NotImplementedError(
        "mock LLM does not support structured output"
    )
    return llm


def make_structured_mock_llm(structured_response: Any) -> MagicMock:
    """Return a MagicMock whose ``with_structured_output`` chain returns
    *structured_response* directly.

    Use this to test the structured-output (primary) path of the
    consolidator without hitting a real LLM.
    """
    inner = MagicMock()
    inner.invoke.return_value = structured_response

    llm = MagicMock()
    llm.with_structured_output.return_value = inner
    return llm


# ---------------------------------------------------------------------------
# Sample domain data fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_stock_price() -> StockPrice:
    return StockPrice(
        symbol="TEL",
        current_price=1250.0,
        currency="PHP",
        fifty_two_week_high=1400.0,
        fifty_two_week_low=1100.0,
        previous_close=1245.0,
    )


@pytest.fixture
def sample_dividend_info() -> DividendInfo:
    return DividendInfo(
        symbol="TEL",
        dividend_rate=75.0,
        dividend_yield=0.06,
        payout_ratio=0.65,
        ex_dividend_date="2025-09-15",
        five_year_avg_yield=5.5,
    )


@pytest.fixture
def sample_price_movement() -> PriceMovement:
    return PriceMovement(
        symbol="TEL",
        year_start_price=1100.0,
        year_end_price=1250.0,
        year_change_pct=13.64,
        max_price=1400.0,
        min_price=1050.0,
        volatility=1.82,
        trend=TrendDirection.UPTREND,
        monthly_prices=[1100, 1120, 1150, 1180, 1200, 1220, 1250, 1280, 1300, 1350, 1380, 1250],
    )


@pytest.fixture
def sample_fair_value() -> FairValueEstimate:
    return FairValueEstimate(
        symbol="TEL",
        current_price=1250.0,
        book_value=800.0,
        pe_ratio=12.5,
        pb_ratio=1.56,
        peg_ratio=1.1,
        forward_pe=11.0,
        estimated_fair_value=1400.0,
        discount_pct=10.71,
    )


@pytest.fixture
def sample_controversy_info() -> ControversyInfo:
    return ControversyInfo(
        symbol="TEL",
        sudden_spikes=["2025-06-10: spike up of 7.2%"],
        risk_factors=["High daily volatility (std > 3%)"],
        recent_news_summary="No automated news feed configured.",
    )


@pytest.fixture
def sample_advisor_state(
    sample_stock_price: StockPrice,
    sample_dividend_info: DividendInfo,
    sample_price_movement: PriceMovement,
    sample_fair_value: FairValueEstimate,
    sample_controversy_info: ControversyInfo,
) -> AdvisorState:
    return AdvisorState(
        symbol="TEL",
        price_analysis=PriceAnalysis(data=sample_stock_price, analysis="Price looks healthy."),
        dividend_analysis=DividendAnalysis(data=sample_dividend_info, analysis="Dividends are good."),
        movement_analysis=MovementAnalysis(data=sample_price_movement, analysis="Trending up."),
        valuation_analysis=ValuationAnalysis(data=sample_fair_value, analysis="Undervalued."),
        controversy_analysis=ControversyAnalysis(data=sample_controversy_info, analysis="Minor risk."),
    )
