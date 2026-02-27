"""
Tests for market data tool functions.

These tests mock DragonFi, PSE EDGE, TradingView and Tavily to avoid network calls.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import numpy as np
import pytest

from ph_stocks_advisor.data.tools import (
    SymbolNotFoundError,
    fetch_controversy_info,
    fetch_dividend_info,
    fetch_fair_value,
    fetch_price_movement,
    fetch_stock_price,
    validate_symbol,
)
from ph_stocks_advisor.data.models import TrendDirection

# Fixed end-date for bdate_range — must be a weekday so pandas 3.x
# doesn't silently drop a non-business-day endpoint.
_BDATE_END = pd.Timestamp("2026-02-27")  # Friday


def _sample_history(start_price: float = 100.0, periods: int = 252) -> pd.DataFrame:
    """Generate a simple upward-trending price history."""
    dates = pd.bdate_range(end=_BDATE_END, periods=periods)
    prices = np.linspace(start_price, start_price * 1.15, periods)
    rng = np.random.default_rng(42)
    noise = rng.normal(0, 1, periods)
    prices = prices + noise
    return pd.DataFrame(
        {"Close": prices, "Open": prices, "High": prices + 1, "Low": prices - 1, "Volume": 1000},
        index=dates,
    )


# ---------------------------------------------------------------------------
# DragonFi mock data
# ---------------------------------------------------------------------------

_DRAGONFI_PROFILE = {
    "stockCode": "TEL",
    "companyName": "PLDT INC.",
    "price": 1250.0,
    "prevDayClosePrice": 1245.0,
    "weekHigh52": 1400.0,
    "weekLow52": 1100.0,
    "dividendYield": 6.0,
    "sharesOutstanding": 216_100_000,
}

_DRAGONFI_VALUATION = {
    "annualValuation": {
        "priceToEarnings": {"Current": 12.5},
        "priceToBook": {"Current": 1.56},
    }
}


# ---------------------------------------------------------------------------
# Stock price
# ---------------------------------------------------------------------------

class TestFetchStockPrice:
    @patch("ph_stocks_advisor.data.services.price.fetch_stock_profile")
    def test_returns_from_dragonfi(self, mock_profile):
        mock_profile.return_value = _DRAGONFI_PROFILE.copy()
        result = fetch_stock_price("TEL")
        assert result.symbol == "TEL"
        assert result.current_price == 1250.0
        assert result.fifty_two_week_high == 1400.0
        assert result.fifty_two_week_low == 1100.0

    @patch("ph_stocks_advisor.data.services.price.fetch_stock_profile")
    def test_catalysts_detected_for_high_yield(self, mock_profile):
        # Price in upper portion of 52-week range + high dividend yield
        mock_profile.return_value = {
            "price": 43.5,
            "prevDayClosePrice": 43.05,
            "weekHigh52": 45.5,
            "weekLow52": 38.0,
            "dividendYield": 5.54,
            "isREIT": True,
        }
        result = fetch_stock_price("AREIT")
        assert len(result.price_catalysts) > 0
        assert any("REIT" in c for c in result.price_catalysts)
        assert any("dividend" in c.lower() for c in result.price_catalysts)

    @patch("ph_stocks_advisor.data.services.price.fetch_stock_profile")
    def test_empty_dragonfi_returns_minimal(self, mock_profile):
        mock_profile.return_value = {}
        result = fetch_stock_price("JFC")
        assert result.current_price == 0.0
        assert result.symbol == "JFC"


# ---------------------------------------------------------------------------
# Dividend info
# ---------------------------------------------------------------------------

class TestFetchDividendInfo:
    @patch("ph_stocks_advisor.data.services.dividend.search_dividend_news", return_value="")
    @patch("ph_stocks_advisor.data.services.dividend.fetch_annual_cashflow_trends")
    @patch("ph_stocks_advisor.data.services.dividend.fetch_annual_income_trends")
    @patch("ph_stocks_advisor.data.services.dividend.fetch_stock_profile")
    def test_returns_from_dragonfi(self, mock_profile, mock_income, mock_cf, _mock_tavily):
        mock_profile.return_value = _DRAGONFI_PROFILE.copy()
        mock_income.return_value = {
            "revenue": {"2022": 5.11e9, "2023": 7.27e9, "2024": 10.26e9},
            "net_income": {"2022": 2.89e9, "2023": 5.03e9, "2024": 7.32e9},
        }
        mock_cf.return_value = {
            "fcf": {"2022": 3.83e9, "2023": 6.44e9, "2024": 5.95e9},
        }
        result = fetch_dividend_info("TEL")
        assert result.dividend_yield == pytest.approx(0.06, abs=0.001)
        # dividend_rate should be yield * price = 0.06 * 1250 = 75
        assert result.dividend_rate == pytest.approx(75.0, abs=0.1)
        assert result.annual_dividend_per_share == pytest.approx(75.0, abs=0.1)
        # payout ratio: (75 * 216_100_000) / 7.32e9 ≈ 2.21
        assert result.payout_ratio > 0
        assert result.net_income_trend["2024"] == 7.32e9
        assert result.revenue_trend["2024"] == 10.26e9
        assert result.free_cash_flow_trend["2024"] == 5.95e9
        assert "Net income grew" in result.dividend_sustainability_note

    @patch("ph_stocks_advisor.data.services.dividend.search_dividend_news", return_value="AREIT declares Q1 2026 dividend of PHP 0.56/share")
    @patch("ph_stocks_advisor.data.services.dividend.fetch_annual_cashflow_trends")
    @patch("ph_stocks_advisor.data.services.dividend.fetch_annual_income_trends")
    @patch("ph_stocks_advisor.data.services.dividend.fetch_stock_profile")
    def test_tavily_dividend_news_included(self, mock_profile, mock_income, mock_cf, _mock_tavily):
        mock_profile.return_value = _DRAGONFI_PROFILE.copy()
        mock_income.return_value = {"net_income": {"2024": 7e9}, "revenue": {}}
        mock_cf.return_value = {"fcf": {}}
        result = fetch_dividend_info("TEL")
        assert "Q1 2026 dividend" in result.recent_dividend_news

    @patch("ph_stocks_advisor.data.services.dividend.search_dividend_news", return_value="")
    @patch("ph_stocks_advisor.data.services.dividend.fetch_annual_cashflow_trends")
    @patch("ph_stocks_advisor.data.services.dividend.fetch_annual_income_trends")
    @patch("ph_stocks_advisor.data.services.dividend.fetch_stock_profile")
    def test_reit_flag_detected(self, mock_profile, mock_income, mock_cf, _mock_tavily):
        reit_profile = _DRAGONFI_PROFILE.copy()
        reit_profile["isREIT"] = True
        mock_profile.return_value = reit_profile
        mock_income.return_value = {"net_income": {"2024": 7e9}, "revenue": {}}
        mock_cf.return_value = {"fcf": {}}
        result = fetch_dividend_info("TEL")
        assert result.is_reit is True
        assert "REIT" in result.dividend_sustainability_note

    @patch("ph_stocks_advisor.data.services.dividend.fetch_stock_profile")
    def test_empty_dragonfi_returns_minimal(self, mock_profile):
        mock_profile.return_value = {"dividendYield": 0}
        result = fetch_dividend_info("TEL")
        assert result.dividend_rate == 0.0
        assert result.symbol == "TEL"


# ---------------------------------------------------------------------------
# Price movement
# ---------------------------------------------------------------------------

class TestFetchPriceMovement:
    @patch("ph_stocks_advisor.data.services.movement.fetch_tradingview_snapshot", return_value={})
    @patch("ph_stocks_advisor.data.services.movement.fetch_pse_edge_ohlcv", return_value=pd.DataFrame())
    @patch("ph_stocks_advisor.data.services.movement.search_stock_news", return_value="")
    @patch("ph_stocks_advisor.data.services.movement.fetch_stock_profile")
    def test_uptrend_detected(self, mock_profile, _web, _pse, _tv):
        mock_profile.return_value = _DRAGONFI_PROFILE.copy()
        # PSE EDGE returns empty, so this goes to DragonFi+TradingView fallback
        # To test uptrend with OHLCV we need PSE EDGE to return data:
        pass  # covered by test_pse_edge_ohlcv_used_as_primary

    @patch("ph_stocks_advisor.data.services.movement.fetch_pse_edge_ohlcv", return_value=pd.DataFrame())
    @patch("ph_stocks_advisor.data.services.movement.fetch_tradingview_snapshot", return_value={})
    @patch("ph_stocks_advisor.data.services.movement.search_stock_news", return_value="")
    @patch("ph_stocks_advisor.data.services.movement.fetch_stock_profile")
    def test_empty_history_uses_dragonfi(self, mock_profile, _web, _tv, _pse):
        mock_profile.return_value = {
            "price": 43.0,
            "weekHigh52": 45.5,
            "weekLow52": 38.0,
        }
        result = fetch_price_movement("AREIT")
        assert result.symbol == "AREIT"
        assert result.max_price == 45.5
        assert result.min_price == 38.0
        assert result.year_end_price == 43.0

    @patch("ph_stocks_advisor.data.services.movement.fetch_pse_edge_ohlcv", return_value=pd.DataFrame())
    @patch("ph_stocks_advisor.data.services.movement.fetch_tradingview_snapshot", return_value={})
    @patch("ph_stocks_advisor.data.services.movement.search_stock_news", return_value="")
    @patch("ph_stocks_advisor.data.services.movement.fetch_stock_profile")
    def test_empty_everything(self, mock_profile, _web, _tv, _pse):
        mock_profile.return_value = {}
        result = fetch_price_movement("XYZ")
        assert result.year_start_price == 0.0
        assert result.trend == TrendDirection.SIDEWAYS

    @patch("ph_stocks_advisor.data.services.movement.fetch_pse_edge_ohlcv", return_value=pd.DataFrame())
    @patch("ph_stocks_advisor.data.services.movement.fetch_tradingview_snapshot", return_value={})
    @patch("ph_stocks_advisor.data.services.movement.search_stock_news", return_value="")
    @patch("ph_stocks_advisor.data.services.movement.fetch_stock_profile")
    def test_catalysts_passed_to_movement(self, mock_profile, _web, _tv, _pse):
        mock_profile.return_value = {
            "price": 43.5,
            "prevDayClosePrice": 43.05,
            "weekHigh52": 45.5,
            "weekLow52": 38.0,
            "dividendYield": 5.54,
            "isREIT": True,
        }
        result = fetch_price_movement("AREIT")
        assert len(result.price_catalysts) > 0
        assert any("dividend" in c.lower() for c in result.price_catalysts)

    @patch("ph_stocks_advisor.data.services.movement.fetch_tradingview_snapshot", return_value={})
    @patch("ph_stocks_advisor.data.services.movement.search_stock_news", return_value="DMC drops on Semirara exposure")
    @patch("ph_stocks_advisor.data.services.movement.fetch_stock_profile")
    @patch("ph_stocks_advisor.data.services.movement.fetch_pse_edge_ohlcv")
    def test_max_drawdown_detected(self, mock_pse, mock_profile, _web, _tv):
        """Simulate a stock that rises then crashes mid-year and partly recovers."""
        mock_profile.return_value = _DRAGONFI_PROFILE.copy()
        dates = pd.bdate_range(end=_BDATE_END, periods=200)
        prices = np.concatenate([
            np.linspace(10.0, 14.0, 80),   # rally to 14
            np.linspace(14.0, 8.0, 40),    # crash to 8 (~43% drawdown)
            np.linspace(8.0, 10.5, 80),    # partial recovery
        ])
        hist = pd.DataFrame({"Close": prices}, index=dates)
        mock_pse.return_value = hist
        result = fetch_price_movement("DMC")
        # Drawdown should be roughly -43% (8 from peak 14)
        assert result.max_drawdown_pct < -30
        assert result.web_news == "DMC drops on Semirara exposure"

    @patch("ph_stocks_advisor.data.services.movement.fetch_pse_edge_ohlcv", return_value=pd.DataFrame())
    @patch("ph_stocks_advisor.data.services.movement.fetch_tradingview_snapshot")
    @patch("ph_stocks_advisor.data.services.movement.search_stock_news", return_value="")
    @patch("ph_stocks_advisor.data.services.movement.fetch_stock_profile")
    def test_tradingview_perf_used_in_fallback(self, mock_profile, _web, mock_tv, _pse):
        """When PSE EDGE is empty, TradingView's 1-year perf should be used."""
        mock_profile.return_value = {
            "price": 9.88,
            "weekHigh52": 11.86,
            "weekLow52": 8.07,
        }
        mock_tv.return_value = {
            "perf_year": -13.94,
            "perf_1m": -9.52,
            "perf_week": 13.69,
            "volatility_monthly": 3.67,
        }
        result = fetch_price_movement("DMC")
        # Should use TV's -13.94% not DragonFi's misleading +22%
        assert result.year_change_pct == pytest.approx(-13.94, abs=0.1)
        assert result.trend == TrendDirection.DOWNTREND
        assert result.volatility == pytest.approx(3.67, abs=0.01)
        assert "1-year: -13.9%" in result.performance_summary
        assert "1-week: +13.7%" in result.performance_summary

    @patch("ph_stocks_advisor.data.services.movement.fetch_tradingview_snapshot", return_value={})
    @patch("ph_stocks_advisor.data.services.movement.search_stock_news", return_value="")
    @patch("ph_stocks_advisor.data.services.movement.fetch_stock_profile")
    @patch("ph_stocks_advisor.data.services.movement.fetch_pse_edge_ohlcv")
    def test_pse_edge_ohlcv_used_as_primary(self, mock_pse, mock_profile, _web, _tv):
        """PSE EDGE OHLCV should be used as the primary data source."""
        mock_profile.return_value = _DRAGONFI_PROFILE.copy()
        dates = pd.bdate_range(end=_BDATE_END, periods=100)
        prices = np.linspace(10.0, 12.0, 100)
        hist = pd.DataFrame({
            "Open": prices - 0.1,
            "High": prices + 0.2,
            "Low": prices - 0.2,
            "Close": prices,
            "Volume": [1_000_000] * 100,
        }, index=dates)
        mock_pse.return_value = hist
        result = fetch_price_movement("DMC")
        assert result.trend == TrendDirection.UPTREND
        assert result.year_change_pct > 15
        assert result.candlestick_patterns  # candlestick analysis activated


# ---------------------------------------------------------------------------
# Fair value
# ---------------------------------------------------------------------------

class TestFetchFairValue:
    @patch("ph_stocks_advisor.data.services.valuation.fetch_security_valuation")
    @patch("ph_stocks_advisor.data.services.valuation.fetch_stock_profile")
    def test_graham_number_from_dragonfi(self, mock_profile, mock_valuation):
        mock_profile.return_value = {"price": 100.0}
        mock_valuation.return_value = {
            "annualValuation": {
                "priceToEarnings": {"Current": 10.0},
                "priceToBook": {"Current": 2.0},
            }
        }
        result = fetch_fair_value("BDO")
        assert result.estimated_fair_value > 0
        assert result.current_price == 100.0
        assert result.pe_ratio == 10.0
        assert result.pb_ratio == 2.0

    @patch("ph_stocks_advisor.data.services.valuation.fetch_security_valuation")
    @patch("ph_stocks_advisor.data.services.valuation.fetch_stock_profile")
    def test_empty_dragonfi_returns_minimal(self, mock_profile, mock_valuation):
        mock_profile.return_value = {"price": 0}
        mock_valuation.return_value = {}
        result = fetch_fair_value("ACEN")
        assert result.current_price == 0.0
        assert result.symbol == "ACEN"


# ---------------------------------------------------------------------------
# Controversy / risk
# ---------------------------------------------------------------------------

class TestFetchControversyInfo:
    @patch("ph_stocks_advisor.data.services.controversy.search_stock_controversies", return_value="")
    @patch("ph_stocks_advisor.data.services.controversy.search_stock_news", return_value="")
    @patch("ph_stocks_advisor.data.services.controversy.fetch_stock_profile", return_value={})
    @patch("ph_stocks_advisor.data.services.controversy.fetch_stock_news")
    @patch("ph_stocks_advisor.data.services.controversy._fetch_history")
    def test_no_spikes_on_calm_data(self, mock_hist, mock_news, _prof, _web, _contr):
        dates = pd.bdate_range(end=_BDATE_END, periods=100)
        prices = np.linspace(100, 105, 100)
        hist = pd.DataFrame({"Close": prices}, index=dates)
        mock_hist.return_value = hist
        mock_news.return_value = []
        result = fetch_controversy_info("SM")
        assert len(result.sudden_spikes) == 0

    @patch("ph_stocks_advisor.data.services.controversy.search_stock_controversies", return_value="")
    @patch("ph_stocks_advisor.data.services.controversy.search_stock_news", return_value="")
    @patch("ph_stocks_advisor.data.services.controversy.fetch_stock_profile", return_value={})
    @patch("ph_stocks_advisor.data.services.controversy.fetch_stock_news")
    @patch("ph_stocks_advisor.data.services.controversy._fetch_history")
    def test_detects_spike(self, mock_hist, mock_news, _prof, _web, _contr):
        dates = pd.bdate_range(end=_BDATE_END, periods=100)
        prices = np.full(100, 100.0)
        prices[50] = 115.0  # 15% jump
        hist = pd.DataFrame({"Close": prices}, index=dates)
        mock_hist.return_value = hist
        mock_news.return_value = []
        result = fetch_controversy_info("ALI")
        assert len(result.sudden_spikes) > 0

    @patch("ph_stocks_advisor.data.services.controversy.search_stock_controversies", return_value="")
    @patch("ph_stocks_advisor.data.services.controversy.search_stock_news", return_value="")
    @patch("ph_stocks_advisor.data.services.controversy.fetch_stock_profile", return_value={})
    @patch("ph_stocks_advisor.data.services.controversy.fetch_stock_news")
    @patch("ph_stocks_advisor.data.services.controversy._fetch_history")
    def test_news_from_dragonfi(self, mock_hist, mock_news, _prof, _web, _contr):
        mock_hist.return_value = pd.DataFrame()
        mock_news.return_value = [
            {"title": "AREIT posts strong earnings", "source": "Manila Times"},
            {"title": "REIT sector outlook positive", "source": "Philstar"},
        ]
        result = fetch_controversy_info("AREIT")
        assert "AREIT posts strong earnings" in result.recent_news_summary
        assert "Manila Times" in result.recent_news_summary

    @patch("ph_stocks_advisor.data.services.controversy.search_stock_controversies", return_value="SEC probes AREIT pricing")
    @patch("ph_stocks_advisor.data.services.controversy.search_stock_news", return_value="AREIT announces record revenue")
    @patch("ph_stocks_advisor.data.services.controversy.fetch_stock_profile", return_value={"companyName": "AREIT INC."})
    @patch("ph_stocks_advisor.data.services.controversy.fetch_stock_news", return_value=[])
    @patch("ph_stocks_advisor.data.services.controversy._fetch_history")
    def test_tavily_web_news_included(self, mock_hist, _dfnews, _prof, _web, _contr):
        mock_hist.return_value = pd.DataFrame()
        result = fetch_controversy_info("AREIT")
        assert "AREIT announces record revenue" in result.web_news
        assert "SEC probes" in result.web_news


# ---------------------------------------------------------------------------
# Validate symbol (now powered by DragonFi)
# ---------------------------------------------------------------------------

class TestValidateSymbol:
    @patch("ph_stocks_advisor.data.clients.dragonfi.validate_pse_symbol")
    def test_valid_symbol_returns_code(self, mock_validate):
        mock_validate.return_value = "TEL"
        result = validate_symbol("TEL")
        assert result == "TEL"

    @patch("ph_stocks_advisor.data.clients.dragonfi.validate_pse_symbol")
    def test_strips_ps_suffix(self, mock_validate):
        mock_validate.return_value = "SM"
        result = validate_symbol("SM.PS")
        assert result == "SM"

    @patch("ph_stocks_advisor.data.tools.validate_pse_symbol")
    def test_invalid_symbol_raises(self, mock_validate):
        mock_validate.side_effect = SymbolNotFoundError("not found")
        with pytest.raises(SymbolNotFoundError, match="not found"):
            validate_symbol("DOESNOTEXIST")


class TestValidatePseSymbolDragonFi:
    """Tests for the DragonFi-based validate_pse_symbol function."""

    @patch("ph_stocks_advisor.data.clients.dragonfi._get")
    @patch("ph_stocks_advisor.data.clients.dragonfi._fetch_all_stock_codes")
    def test_found_in_stock_list(self, mock_codes, mock_get):
        mock_codes.return_value = frozenset({"AREIT", "TEL", "SM"})
        from ph_stocks_advisor.data.clients.dragonfi import validate_pse_symbol
        result = validate_pse_symbol("AREIT")
        assert result == "AREIT"

    @patch("ph_stocks_advisor.data.clients.dragonfi._get")
    @patch("ph_stocks_advisor.data.clients.dragonfi._fetch_all_stock_codes")
    def test_fallback_to_profile(self, mock_codes, mock_get):
        mock_codes.return_value = frozenset()
        mock_get.return_value = {"stockCode": "AREIT"}
        from ph_stocks_advisor.data.clients.dragonfi import validate_pse_symbol
        result = validate_pse_symbol("AREIT")
        assert result == "AREIT"

    @patch("ph_stocks_advisor.data.clients.dragonfi._get")
    @patch("ph_stocks_advisor.data.clients.dragonfi._fetch_all_stock_codes")
    def test_not_found_raises(self, mock_codes, mock_get):
        mock_codes.return_value = frozenset()
        mock_get.return_value = None
        from ph_stocks_advisor.data.clients.dragonfi import validate_pse_symbol
        with pytest.raises(SymbolNotFoundError, match="not listed"):
            validate_pse_symbol("DOESNOTEXIST")


# ---------------------------------------------------------------------------
# Price catalyst detection
# ---------------------------------------------------------------------------

class TestDetectPriceCatalysts:
    def test_reit_dividend_catalyst(self):
        from ph_stocks_advisor.data.services.price import detect_price_catalysts
        profile = {
            "price": 43.5,
            "prevDayClosePrice": 43.05,
            "weekHigh52": 45.5,
            "weekLow52": 38.0,
            "dividendYield": 5.54,
            "isREIT": True,
        }
        catalysts = detect_price_catalysts(profile)
        assert any("REIT" in c for c in catalysts)
        assert any("dividend" in c.lower() for c in catalysts)

    def test_high_yield_non_reit(self):
        from ph_stocks_advisor.data.services.price import detect_price_catalysts
        profile = {
            "price": 1250.0,
            "prevDayClosePrice": 1240.0,
            "weekHigh52": 1400.0,
            "weekLow52": 1100.0,
            "dividendYield": 6.0,
            "isREIT": False,
        }
        catalysts = detect_price_catalysts(profile)
        assert any("dividend" in c.lower() for c in catalysts)
        assert not any("REIT" in c for c in catalysts)

    def test_no_catalyst_for_low_yield(self):
        from ph_stocks_advisor.data.services.price import detect_price_catalysts
        profile = {
            "price": 50.0,
            "prevDayClosePrice": 49.5,
            "weekHigh52": 60.0,
            "weekLow52": 40.0,
            "dividendYield": 1.0,
            "isREIT": False,
        }
        catalysts = detect_price_catalysts(profile)
        # Low yield + not near 52-week high → no dividend catalyst
        assert not any("dividend" in c.lower() for c in catalysts)

    def test_near_52_week_high(self):
        from ph_stocks_advisor.data.services.price import detect_price_catalysts
        profile = {
            "price": 59.0,
            "prevDayClosePrice": 58.5,
            "weekHigh52": 60.0,
            "weekLow52": 40.0,
            "dividendYield": 0.5,
            "isREIT": False,
        }
        catalysts = detect_price_catalysts(profile)
        assert any("52-week high" in c for c in catalysts)

    def test_empty_profile(self):
        from ph_stocks_advisor.data.services.price import detect_price_catalysts
        assert detect_price_catalysts({}) == []
        assert detect_price_catalysts(None) == []


# ---------------------------------------------------------------------------
# DragonFi financial trend helpers
# ---------------------------------------------------------------------------

class TestExtractAnnualValues:
    def test_extracts_year_values(self):
        from ph_stocks_advisor.data.clients.dragonfi import _extract_annual_values
        data = {
            "Symbol": "AREIT",
            "Item": "Net Income",
            "2022_YoY": "18.93 %",
            "2022": 2890000000.0,
            "2023_YoY": "74.05 %",
            "2023": 5030000000.0,
            "2024_YoY": "45.47 %",
            "2024": 7317064704.0,
        }
        result = _extract_annual_values(data)
        assert result == {"2022": 2890000000.0, "2023": 5030000000.0, "2024": 7317064704.0}

    def test_returns_empty_for_none(self):
        from ph_stocks_advisor.data.clients.dragonfi import _extract_annual_values
        assert _extract_annual_values(None) == {}

    def test_skips_none_values(self):
        from ph_stocks_advisor.data.clients.dragonfi import _extract_annual_values
        data = {"2022": 100.0, "2023": None, "2024": 200.0}
        result = _extract_annual_values(data)
        assert result == {"2022": 100.0, "2024": 200.0}


class TestFetchAnnualIncomeTrends:
    @patch("ph_stocks_advisor.data.clients.dragonfi.fetch_stock_financials")
    def test_returns_revenue_and_net_income(self, mock_fin):
        mock_fin.return_value = {
            "incomeStatementAnnual": {
                "revenue": {"Symbol": "X", "Item": "Revenue", "2023": 7e9, "2024": 10e9},
                "netIncome": {"Symbol": "X", "Item": "NI", "2023": 5e9, "2024": 7e9},
                "operationIncome": {"Symbol": "X", "Item": "OI", "2023": 4e9, "2024": 6e9},
            }
        }
        from ph_stocks_advisor.data.clients.dragonfi import fetch_annual_income_trends
        result = fetch_annual_income_trends("X")
        assert result["revenue"] == {"2023": 7e9, "2024": 10e9}
        assert result["net_income"] == {"2023": 5e9, "2024": 7e9}

    @patch("ph_stocks_advisor.data.clients.dragonfi.fetch_stock_financials")
    def test_returns_empty_on_no_data(self, mock_fin):
        mock_fin.return_value = {}
        from ph_stocks_advisor.data.clients.dragonfi import fetch_annual_income_trends
        result = fetch_annual_income_trends("X")
        assert result == {}


# ---------------------------------------------------------------------------
# PSE EDGE OHLCV module
# ---------------------------------------------------------------------------

class TestPseEdge:
    """Tests for pse_edge.py data fetching."""

    @patch("ph_stocks_advisor.data.clients.pse_edge.requests.get")
    def test_resolve_cmpy_id(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: [{"cmpyId": "188", "cmpyNm": "DMCI Holdings, Inc.", "symbol": "DMC"}],
        )
        from ph_stocks_advisor.data.clients.pse_edge import _resolve_cmpy_id
        assert _resolve_cmpy_id("DMC") == "188"

    @patch("ph_stocks_advisor.data.clients.pse_edge.requests.get")
    def test_resolve_cmpy_id_no_match(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: [{"cmpyId": "154", "cmpyNm": "San Miguel Corp", "symbol": "SMC"}],
        )
        from ph_stocks_advisor.data.clients.pse_edge import _resolve_cmpy_id
        assert _resolve_cmpy_id("SM") is None

    @patch("ph_stocks_advisor.data.clients.pse_edge.requests.get")
    def test_resolve_security_id(self, mock_get):
        html = '''<select name="security_id" onchange="document.form1.submit();">
<option value="192" selected>DMC</option>
<option value="261" >DMCP</option>
</select>'''
        mock_get.return_value = MagicMock(status_code=200, text=html)
        from ph_stocks_advisor.data.clients.pse_edge import _resolve_security_id
        assert _resolve_security_id("188") == "192"

    @patch("ph_stocks_advisor.data.clients.pse_edge.requests.post")
    @patch("ph_stocks_advisor.data.clients.pse_edge._resolve_ids")
    def test_fetch_ohlcv_success(self, mock_ids, mock_post):
        mock_ids.return_value = ("188", "192")
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "chartData": [
                    {"OPEN": 11.48, "HIGH": 11.58, "LOW": 11.28, "CLOSE": 11.5,
                     "VALUE": 4.867e7, "CHART_DATE": "Feb 26, 2025 00:00:00"},
                    {"OPEN": 11.5, "HIGH": 11.54, "LOW": 11.38, "CLOSE": 11.5,
                     "VALUE": 3.683e7, "CHART_DATE": "Feb 27, 2025 00:00:00"},
                ],
                "tableData": [],
            },
        )
        from ph_stocks_advisor.data.clients.pse_edge import fetch_pse_edge_ohlcv
        df = fetch_pse_edge_ohlcv("DMC")
        assert len(df) == 2
        assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
        assert df.iloc[0]["Close"] == 11.5

    @patch("ph_stocks_advisor.data.clients.pse_edge._resolve_ids", return_value=None)
    def test_fetch_ohlcv_unresolved_returns_empty(self, _ids):
        from ph_stocks_advisor.data.clients.pse_edge import fetch_pse_edge_ohlcv
        df = fetch_pse_edge_ohlcv("ZZZ")
        assert df.empty

    @patch("ph_stocks_advisor.data.clients.pse_edge.requests.post")
    @patch("ph_stocks_advisor.data.clients.pse_edge._resolve_ids")
    def test_fetch_ohlcv_deduplicates(self, mock_ids, mock_post):
        """PSE EDGE sometimes returns duplicate rows — verify deduplication."""
        mock_ids.return_value = ("188", "192")
        row = {"OPEN": 11.0, "HIGH": 11.2, "LOW": 10.9, "CLOSE": 11.1,
               "VALUE": 1e7, "CHART_DATE": "Mar 24, 2025 00:00:00"}
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"chartData": [row, row], "tableData": []},
        )
        from ph_stocks_advisor.data.clients.pse_edge import fetch_pse_edge_ohlcv
        df = fetch_pse_edge_ohlcv("DMC")
        assert len(df) == 1

    @patch("ph_stocks_advisor.data.clients.pse_edge.requests.post")
    @patch("ph_stocks_advisor.data.clients.pse_edge._resolve_ids")
    def test_fetch_ohlcv_http_error(self, mock_ids, mock_post):
        mock_ids.return_value = ("188", "192")
        mock_post.return_value = MagicMock(status_code=500)
        from ph_stocks_advisor.data.clients.pse_edge import fetch_pse_edge_ohlcv
        df = fetch_pse_edge_ohlcv("DMC")
        assert df.empty


# ---------------------------------------------------------------------------
# TradingView scanner module
# ---------------------------------------------------------------------------

class TestTradingView:
    """Tests for tradingview.py data fetching."""

    @patch("ph_stocks_advisor.data.clients.tradingview.requests.post")
    def test_fetch_snapshot_success(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "totalCount": 1,
                "data": [{"s": "PSE:DMC", "d": [
                    9.88, 9.81, 9.93, 9.75, 2240900,
                    13.69, -9.52, -5.73, -6.97, -13.94, -6.44,
                    1.85, 3.14, 3.67,
                    11.86, 8.07,
                ]}],
            },
        )
        from ph_stocks_advisor.data.clients.tradingview import fetch_tradingview_snapshot
        result = fetch_tradingview_snapshot("DMC")
        assert result["close"] == 9.88
        assert result["perf_year"] == -13.94
        assert result["volatility_monthly"] == 3.67
        assert result["week_high_52"] == 11.86

    @patch("ph_stocks_advisor.data.clients.tradingview.requests.post")
    def test_fetch_snapshot_failure(self, mock_post):
        mock_post.return_value = MagicMock(status_code=500)
        from ph_stocks_advisor.data.clients.tradingview import fetch_tradingview_snapshot
        result = fetch_tradingview_snapshot("XYZ")
        assert result == {}

    def test_format_performance_summary(self):
        from ph_stocks_advisor.data.clients.tradingview import format_tv_performance_summary
        snap = {
            "perf_week": 13.69,
            "perf_1m": -9.52,
            "perf_3m": -5.73,
            "perf_6m": -6.97,
            "perf_year": -13.94,
            "perf_ytd": -6.44,
            "volatility_monthly": 3.67,
        }
        text = format_tv_performance_summary(snap)
        assert "1-week: +13.7%" in text
        assert "1-month: -9.5%" in text
        assert "1-year: -13.9%" in text
        assert "volatility: 3.7%" in text

    def test_format_empty_snapshot(self):
        from ph_stocks_advisor.data.clients.tradingview import format_tv_performance_summary
        assert format_tv_performance_summary({}) == ""


# ---------------------------------------------------------------------------
# Candlestick analysis module
# ---------------------------------------------------------------------------

class TestCandlestickAnalysis:
    """Tests for candlestick.py pattern detection."""

    def _make_ohlcv(self, n: int = 100, *, base: float = 10.0) -> pd.DataFrame:
        """Create a calm OHLCV DataFrame."""
        dates = pd.bdate_range(end=_BDATE_END, periods=n)
        closes = np.linspace(base, base * 1.05, n)
        return pd.DataFrame({
            "Open": closes * 0.999,
            "High": closes * 1.005,
            "Low": closes * 0.995,
            "Close": closes,
            "Volume": np.full(n, 1_000_000),
        }, index=dates)

    def test_no_patterns_on_calm_data(self):
        from ph_stocks_advisor.data.analysis.candlestick import analyse_candlesticks
        df = self._make_ohlcv()
        summary = analyse_candlesticks(df)
        assert summary.notable_candles == []
        assert summary.gap_events == []
        assert summary.volume_spikes == []
        assert summary.selling_pressure_periods == []
        # Steady uptrend generates all-bullish candles — that's expected
        assert len(summary.buying_pressure_periods) >= 1

    def test_detects_large_bearish_candle(self):
        from ph_stocks_advisor.data.analysis.candlestick import analyse_candlesticks
        df = self._make_ohlcv(100)
        # Inject a -10% bearish candle at position 50
        df.iloc[50, df.columns.get_loc("Open")] = 12.0
        df.iloc[50, df.columns.get_loc("Close")] = 10.5
        df.iloc[50, df.columns.get_loc("High")] = 12.1
        df.iloc[50, df.columns.get_loc("Low")] = 10.3
        summary = analyse_candlesticks(df)
        assert len(summary.notable_candles) >= 1
        assert "bearish" in summary.notable_candles[0].lower()

    def test_detects_gap_down(self):
        from ph_stocks_advisor.data.analysis.candlestick import analyse_candlesticks
        df = self._make_ohlcv(100)
        # Create gap-down: prev close 11, next open 10.5 (~4.5% gap)
        df.iloc[49, df.columns.get_loc("Close")] = 11.0
        df.iloc[50, df.columns.get_loc("Open")] = 10.5
        summary = analyse_candlesticks(df)
        assert len(summary.gap_events) >= 1
        assert "gap-DOWN" in summary.gap_events[0]

    def test_detects_volume_spike(self):
        from ph_stocks_advisor.data.analysis.candlestick import analyse_candlesticks
        df = self._make_ohlcv(100)
        # Inject 5x volume spike at position 80
        df.iloc[80, df.columns.get_loc("Volume")] = 5_000_000
        summary = analyse_candlesticks(df)
        assert len(summary.volume_spikes) >= 1
        assert "spike" in summary.volume_spikes[0].lower()

    def test_detects_selling_pressure(self):
        from ph_stocks_advisor.data.analysis.candlestick import _detect_consecutive_pressure
        dates = pd.bdate_range(end=_BDATE_END, periods=10)
        # 5 consecutive bearish candles (close < open)
        df = pd.DataFrame({
            "Open":  [10, 10, 10, 10, 10, 10, 10, 10, 10, 10],
            "High":  [11, 11, 11, 11, 11, 11, 11, 11, 11, 11],
            "Low":   [9,  9,  9,  9,  9,  9,  9,  9,  9,  9],
            "Close": [9.5, 9.3, 9.2, 9.1, 9.0, 10.5, 10.5, 10.5, 10.5, 10.5],
        }, index=dates, dtype=float)
        selling, buying = _detect_consecutive_pressure(df, min_streak=3)
        assert len(selling) >= 1
        assert "bearish" in selling[0].lower()
        assert len(buying) >= 1
        assert "bullish" in buying[0].lower()

    def test_empty_dataframe(self):
        from ph_stocks_advisor.data.analysis.candlestick import analyse_candlesticks
        summary = analyse_candlesticks(pd.DataFrame())
        assert summary.to_text() == "No notable candlestick patterns detected."

    def test_to_text_formatting(self):
        from ph_stocks_advisor.data.analysis.candlestick import CandlestickSummary
        s = CandlestickSummary(
            notable_candles=["2026-02-10: Large bearish candle"],
            volume_spikes=["2026-02-10: Volume spike 5.0x"],
        )
        text = s.to_text()
        assert "Notable Candles" in text
        assert "Volume Spikes" in text
        assert "bearish" in text


# ---------------------------------------------------------------------------
# Tavily search module
# ---------------------------------------------------------------------------

class TestTavilySearch:
    """Tests for tavily_search.py helper functions."""

    @patch("ph_stocks_advisor.data.clients.tavily_search._get_client", return_value=None)
    def test_search_returns_empty_when_no_client(self, _mock_client):
        from ph_stocks_advisor.data.clients.tavily_search import _search
        assert _search("any query") == []

    @patch("ph_stocks_advisor.data.clients.tavily_search._get_client")
    def test_search_calls_tavily(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.search.return_value = {
            "results": [
                {"title": "Test", "url": "https://example.com", "content": "body", "score": 0.9}
            ]
        }
        mock_get_client.return_value = mock_client
        from ph_stocks_advisor.data.clients.tavily_search import _search
        results = _search("test query", max_results=3)
        assert len(results) == 1
        assert results[0]["title"] == "Test"
        mock_client.search.assert_called_once()

    @patch("ph_stocks_advisor.data.clients.tavily_search._search")
    def test_search_dividend_news_formats_results(self, mock_search):
        mock_search.return_value = [
            {"title": "AREIT declares dividend", "url": "https://example.com", "content": "PHP 0.56/share", "score": 0.8},
        ]
        from ph_stocks_advisor.data.clients.tavily_search import search_dividend_news
        result = search_dividend_news("AREIT", company_name="AREIT Inc.")
        assert "AREIT declares dividend" in result
        assert "PHP 0.56/share" in result

    @patch("ph_stocks_advisor.data.clients.tavily_search._search", return_value=[])
    def test_search_dividend_news_empty(self, _mock):
        from ph_stocks_advisor.data.clients.tavily_search import search_dividend_news
        result = search_dividend_news("XYZ")
        assert "No recent dividend news" in result

    @patch("ph_stocks_advisor.data.clients.tavily_search._search")
    def test_search_stock_controversies(self, mock_search):
        mock_search.return_value = [
            {"title": "SEC inquiry", "url": "https://x.com", "content": "Probe ongoing", "score": 0.7},
        ]
        from ph_stocks_advisor.data.clients.tavily_search import search_stock_controversies
        result = search_stock_controversies("TEL", company_name="PLDT Inc.")
        assert "SEC inquiry" in result

    @patch("ph_stocks_advisor.data.clients.tavily_search._search")
    def test_format_results_fallback(self, mock_search):
        mock_search.return_value = []
        from ph_stocks_advisor.data.clients.tavily_search import search_stock_news
        result = search_stock_news("XYZ")
        assert "No recent news" in result

