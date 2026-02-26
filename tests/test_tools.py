"""
Tests for market data tool functions.

These tests mock DragonFi and yfinance to avoid network calls.
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


def _make_mock_ticker(info: dict | None = None, history_df: pd.DataFrame | None = None):
    """Create a mock yfinance Ticker."""
    mock = MagicMock()
    mock.info = info or {}
    if history_df is not None:
        mock.history.return_value = history_df
    else:
        mock.history.return_value = pd.DataFrame()
    return mock


def _sample_history(start_price: float = 100.0, periods: int = 252) -> pd.DataFrame:
    """Generate a simple upward-trending price history."""
    dates = pd.bdate_range(end=pd.Timestamp.now(), periods=periods)
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
    @patch("ph_stocks_advisor.data.tools.fetch_stock_profile")
    def test_returns_from_dragonfi(self, mock_profile):
        mock_profile.return_value = _DRAGONFI_PROFILE.copy()
        result = fetch_stock_price("TEL")
        assert result.symbol == "TEL"
        assert result.current_price == 1250.0
        assert result.fifty_two_week_high == 1400.0
        assert result.fifty_two_week_low == 1100.0

    @patch("ph_stocks_advisor.data.tools._ticker")
    @patch("ph_stocks_advisor.data.tools.fetch_stock_profile")
    def test_fallback_to_yfinance(self, mock_profile, mock_ticker_fn):
        mock_profile.return_value = {}
        mock_ticker_fn.return_value = _make_mock_ticker(
            info={
                "currentPrice": 50.0,
                "currency": "PHP",
                "fiftyTwoWeekHigh": 60.0,
                "fiftyTwoWeekLow": 40.0,
                "previousClose": 49.0,
            }
        )
        result = fetch_stock_price("JFC")
        assert result.current_price == 50.0
        assert result.fifty_two_week_high == 60.0


# ---------------------------------------------------------------------------
# Dividend info
# ---------------------------------------------------------------------------

class TestFetchDividendInfo:
    @patch("ph_stocks_advisor.data.tools.fetch_security_metrics")
    @patch("ph_stocks_advisor.data.tools.fetch_stock_profile")
    def test_returns_from_dragonfi(self, mock_profile, mock_metrics):
        mock_profile.return_value = _DRAGONFI_PROFILE.copy()
        mock_metrics.return_value = {}
        result = fetch_dividend_info("TEL")
        assert result.dividend_yield == pytest.approx(0.06, abs=0.001)

    @patch("ph_stocks_advisor.data.tools._ticker")
    @patch("ph_stocks_advisor.data.tools.fetch_security_metrics")
    @patch("ph_stocks_advisor.data.tools.fetch_stock_profile")
    def test_fallback_to_yfinance(self, mock_profile, mock_metrics, mock_ticker_fn):
        mock_profile.return_value = {"dividendYield": 0}
        mock_metrics.return_value = {}
        mock_ticker_fn.return_value = _make_mock_ticker(
            info={
                "dividendRate": 75.0,
                "dividendYield": 0.06,
                "payoutRatio": 0.65,
                "fiveYearAvgDividendYield": 5.5,
            }
        )
        result = fetch_dividend_info("TEL")
        assert result.dividend_rate == 75.0
        assert result.dividend_yield == 0.06


# ---------------------------------------------------------------------------
# Price movement
# ---------------------------------------------------------------------------

class TestFetchPriceMovement:
    @patch("ph_stocks_advisor.data.tools._yf_history")
    def test_uptrend_detected(self, mock_hist):
        mock_hist.return_value = _sample_history(100.0, 252)
        result = fetch_price_movement("TEL")
        assert result.trend == TrendDirection.UPTREND
        assert result.year_change_pct > 5

    @patch("ph_stocks_advisor.data.tools.fetch_stock_profile")
    @patch("ph_stocks_advisor.data.tools._yf_history")
    def test_empty_history_uses_dragonfi(self, mock_hist, mock_profile):
        mock_hist.return_value = pd.DataFrame()
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
        assert result.trend == TrendDirection.UPTREND

    @patch("ph_stocks_advisor.data.tools.fetch_stock_profile")
    @patch("ph_stocks_advisor.data.tools._yf_history")
    def test_empty_everything(self, mock_hist, mock_profile):
        mock_hist.return_value = pd.DataFrame()
        mock_profile.return_value = {}
        result = fetch_price_movement("XYZ")
        assert result.year_start_price == 0.0
        assert result.trend == TrendDirection.SIDEWAYS


# ---------------------------------------------------------------------------
# Fair value
# ---------------------------------------------------------------------------

class TestFetchFairValue:
    @patch("ph_stocks_advisor.data.tools.fetch_security_valuation")
    @patch("ph_stocks_advisor.data.tools.fetch_stock_profile")
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

    @patch("ph_stocks_advisor.data.tools._ticker")
    @patch("ph_stocks_advisor.data.tools.fetch_security_valuation")
    @patch("ph_stocks_advisor.data.tools.fetch_stock_profile")
    def test_fallback_to_yfinance(self, mock_profile, mock_valuation, mock_ticker_fn):
        mock_profile.return_value = {"price": 0}
        mock_valuation.return_value = {}
        mock_ticker_fn.return_value = _make_mock_ticker(
            info={
                "currentPrice": 100.0,
                "bookValue": 0.0,
                "trailingPE": 20.0,
                "trailingEps": 0.0,
            }
        )
        result = fetch_fair_value("ACEN")
        # Fallback: (100/20)*15 = 75
        assert result.estimated_fair_value == 75.0


# ---------------------------------------------------------------------------
# Controversy / risk
# ---------------------------------------------------------------------------

class TestFetchControversyInfo:
    @patch("ph_stocks_advisor.data.tools.fetch_stock_news")
    @patch("ph_stocks_advisor.data.tools._yf_history")
    def test_no_spikes_on_calm_data(self, mock_hist, mock_news):
        dates = pd.bdate_range(end=pd.Timestamp.now(), periods=100)
        prices = np.linspace(100, 105, 100)
        hist = pd.DataFrame({"Close": prices}, index=dates)
        mock_hist.return_value = hist
        mock_news.return_value = []
        result = fetch_controversy_info("SM")
        assert len(result.sudden_spikes) == 0

    @patch("ph_stocks_advisor.data.tools.fetch_stock_news")
    @patch("ph_stocks_advisor.data.tools._yf_history")
    def test_detects_spike(self, mock_hist, mock_news):
        dates = pd.bdate_range(end=pd.Timestamp.now(), periods=100)
        prices = np.full(100, 100.0)
        prices[50] = 115.0  # 15% jump
        hist = pd.DataFrame({"Close": prices}, index=dates)
        mock_hist.return_value = hist
        mock_news.return_value = []
        result = fetch_controversy_info("ALI")
        assert len(result.sudden_spikes) > 0

    @patch("ph_stocks_advisor.data.tools.fetch_stock_news")
    @patch("ph_stocks_advisor.data.tools._yf_history")
    def test_news_from_dragonfi(self, mock_hist, mock_news):
        mock_hist.return_value = pd.DataFrame()
        mock_news.return_value = [
            {"title": "AREIT posts strong earnings", "source": "Manila Times"},
            {"title": "REIT sector outlook positive", "source": "Philstar"},
        ]
        result = fetch_controversy_info("AREIT")
        assert "AREIT posts strong earnings" in result.recent_news_summary
        assert "Manila Times" in result.recent_news_summary


# ---------------------------------------------------------------------------
# Validate symbol (now powered by DragonFi)
# ---------------------------------------------------------------------------

class TestValidateSymbol:
    @patch("ph_stocks_advisor.data.tools.validate_pse_symbol")
    def test_valid_symbol_returns_code(self, mock_validate):
        mock_validate.return_value = "TEL"
        result = validate_symbol("TEL")
        assert result == "TEL"

    @patch("ph_stocks_advisor.data.tools.validate_pse_symbol")
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

    @patch("ph_stocks_advisor.data.dragonfi._get")
    @patch("ph_stocks_advisor.data.dragonfi._fetch_all_stock_codes")
    def test_found_in_stock_list(self, mock_codes, mock_get):
        mock_codes.return_value = frozenset({"AREIT", "TEL", "SM"})
        from ph_stocks_advisor.data.dragonfi import validate_pse_symbol
        result = validate_pse_symbol("AREIT")
        assert result == "AREIT"

    @patch("ph_stocks_advisor.data.dragonfi._get")
    @patch("ph_stocks_advisor.data.dragonfi._fetch_all_stock_codes")
    def test_fallback_to_profile(self, mock_codes, mock_get):
        mock_codes.return_value = frozenset()
        mock_get.return_value = {"stockCode": "AREIT"}
        from ph_stocks_advisor.data.dragonfi import validate_pse_symbol
        result = validate_pse_symbol("AREIT")
        assert result == "AREIT"

    @patch("ph_stocks_advisor.data.dragonfi._get")
    @patch("ph_stocks_advisor.data.dragonfi._fetch_all_stock_codes")
    def test_not_found_raises(self, mock_codes, mock_get):
        mock_codes.return_value = frozenset()
        mock_get.return_value = None
        from ph_stocks_advisor.data.dragonfi import validate_pse_symbol
        with pytest.raises(SymbolNotFoundError, match="not listed"):
            validate_pse_symbol("DOESNOTEXIST")
