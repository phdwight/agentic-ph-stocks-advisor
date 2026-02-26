"""
Tests for specialist agents.

All tests mock the LLM and the data-fetching tools so they run
without network access or API keys.
"""

from __future__ import annotations

from unittest.mock import patch

from tests.conftest import make_mock_llm
from ph_stocks_advisor.agents.specialists import (
    ControversyAgent,
    DividendAgent,
    MovementAgent,
    PriceAgent,
    ValuationAgent,
)
from ph_stocks_advisor.data.models import (
    ControversyInfo,
    DividendInfo,
    FairValueEstimate,
    PriceMovement,
    StockPrice,
)


class TestPriceAgent:
    @patch("ph_stocks_advisor.agents.specialists.fetch_stock_price")
    def test_run_returns_analysis(self, mock_fetch, sample_stock_price):
        mock_fetch.return_value = sample_stock_price
        llm = make_mock_llm("Price is near 52-week midpoint.")
        agent = PriceAgent(llm)
        result = agent.run("TEL")
        assert result.data.symbol == "TEL"
        assert "52-week" in result.analysis
        llm.invoke.assert_called_once()


class TestDividendAgent:
    @patch("ph_stocks_advisor.agents.specialists.fetch_dividend_info")
    def test_run_returns_analysis(self, mock_fetch, sample_dividend_info):
        mock_fetch.return_value = sample_dividend_info
        llm = make_mock_llm("Dividend yield is attractive at 6%.")
        agent = DividendAgent(llm)
        result = agent.run("TEL")
        assert result.data.dividend_yield == 0.06
        assert "attractive" in result.analysis


class TestMovementAgent:
    @patch("ph_stocks_advisor.agents.specialists.fetch_price_movement")
    def test_run_returns_analysis(self, mock_fetch, sample_price_movement):
        mock_fetch.return_value = sample_price_movement
        llm = make_mock_llm("Stock has been in an uptrend.")
        agent = MovementAgent(llm)
        result = agent.run("TEL")
        assert result.data.trend.value == "uptrend"
        assert "uptrend" in result.analysis


class TestValuationAgent:
    @patch("ph_stocks_advisor.agents.specialists.fetch_fair_value")
    def test_run_returns_analysis(self, mock_fetch, sample_fair_value):
        mock_fetch.return_value = sample_fair_value
        llm = make_mock_llm("Stock appears undervalued by 10%.")
        agent = ValuationAgent(llm)
        result = agent.run("TEL")
        assert result.data.discount_pct > 0
        assert "undervalued" in result.analysis


class TestControversyAgent:
    @patch("ph_stocks_advisor.agents.specialists.fetch_controversy_info")
    def test_run_returns_analysis(self, mock_fetch, sample_controversy_info):
        mock_fetch.return_value = sample_controversy_info
        llm = make_mock_llm("One notable spike but overall manageable risk.")
        agent = ControversyAgent(llm)
        result = agent.run("TEL")
        assert len(result.data.sudden_spikes) == 1
        assert "spike" in result.analysis
