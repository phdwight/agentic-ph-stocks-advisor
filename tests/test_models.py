"""
Tests for Pydantic data models.
"""

from ph_stocks_advisor.data.models import (
    AdvisorState,
    FinalReport,
    StockPrice,
    TrendDirection,
    Verdict,
)


class TestStockPrice:
    def test_create_with_required_fields(self):
        sp = StockPrice(symbol="TEL", current_price=1250.0)
        assert sp.symbol == "TEL"
        assert sp.current_price == 1250.0
        assert sp.currency == "PHP"

    def test_defaults(self):
        sp = StockPrice(symbol="SM", current_price=900.0)
        assert sp.fifty_two_week_high == 0.0
        assert sp.fifty_two_week_low == 0.0


class TestVerdict:
    def test_buy(self):
        assert Verdict.BUY.value == "BUY"

    def test_not_buy(self):
        assert Verdict.NOT_BUY.value == "NOT BUY"


class TestTrendDirection:
    def test_values(self):
        assert TrendDirection.UPTREND.value == "uptrend"
        assert TrendDirection.DOWNTREND.value == "downtrend"
        assert TrendDirection.SIDEWAYS.value == "sideways"


class TestAdvisorState:
    def test_default_state(self):
        state = AdvisorState(symbol="BDO")
        assert state.symbol == "BDO"
        assert state.price_analysis is None
        assert state.final_report is None


class TestFinalReport:
    def test_creation(self):
        report = FinalReport(
            symbol="TEL",
            verdict=Verdict.BUY,
            summary="Good stock overall.",
        )
        assert report.verdict == Verdict.BUY
        assert "Good stock" in report.summary
