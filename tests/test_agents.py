"""
Tests for specialist agents.

All tests mock the LLM and the data-fetching tools so they run
without network access or API keys.  Canned LLM responses live in
``tests/dummy_responses.py``.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tests.conftest import make_mock_llm
from tests.dummy_responses import (
    CONTROVERSY_ANALYSIS_RESPONSE,
    DIVIDEND_ANALYSIS_RESPONSE,
    MOVEMENT_ANALYSIS_RESPONSE,
    PRICE_ANALYSIS_RESPONSE,
    SENTIMENT_ANALYSIS_RESPONSE,
    VALUATION_ANALYSIS_RESPONSE,
)
from ph_stocks_advisor.agents.specialists import (
    ControversyAgent,
    DividendAgent,
    MovementAgent,
    PriceAgent,
    SentimentAgent,
    ValuationAgent,
)


@pytest.mark.parametrize(
    "agent_cls,patch_target,fixture_name,response_text,assertion",
    [
        (
            PriceAgent,
            "ph_stocks_advisor.agents.specialists.fetch_stock_price",
            "sample_stock_price",
            PRICE_ANALYSIS_RESPONSE,
            lambda r: r.data.symbol == "TEL" and "52-week" in r.analysis,
        ),
        (
            DividendAgent,
            "ph_stocks_advisor.agents.specialists.fetch_dividend_info",
            "sample_dividend_info",
            DIVIDEND_ANALYSIS_RESPONSE,
            lambda r: r.data.dividend_yield == 0.06 and "attractive" in r.analysis,
        ),
        (
            MovementAgent,
            "ph_stocks_advisor.agents.specialists.fetch_price_movement",
            "sample_price_movement",
            MOVEMENT_ANALYSIS_RESPONSE,
            lambda r: r.data.trend.value == "uptrend" and "uptrend" in r.analysis,
        ),
        (
            ValuationAgent,
            "ph_stocks_advisor.agents.specialists.fetch_fair_value",
            "sample_fair_value",
            VALUATION_ANALYSIS_RESPONSE,
            lambda r: r.data.discount_pct > 0 and "undervalued" in r.analysis,
        ),
        (
            ControversyAgent,
            "ph_stocks_advisor.agents.specialists.fetch_controversy_info",
            "sample_controversy_info",
            CONTROVERSY_ANALYSIS_RESPONSE,
            lambda r: len(r.data.sudden_spikes) == 1 and "spike" in r.analysis,
        ),
        (
            SentimentAgent,
            "ph_stocks_advisor.agents.specialists.fetch_sentiment_info",
            "sample_sentiment_info",
            SENTIMENT_ANALYSIS_RESPONSE,
            lambda r: r.data.symbol == "TEL" and "Neutral" in r.analysis,
        ),
    ],
    ids=["price", "dividend", "movement", "valuation", "controversy", "sentiment"],
)
def test_agent_run_returns_analysis(
    agent_cls,
    patch_target,
    fixture_name,
    response_text,
    assertion,
    request,
):
    """Each specialist agent fetches data, invokes the LLM, and returns analysis."""
    sample_data = request.getfixturevalue(fixture_name)
    with patch(patch_target, return_value=sample_data):
        llm = make_mock_llm(response_text)
        agent = agent_cls(llm)
        result = agent.run("TEL")
        assert assertion(result), f"Assertion failed for {agent_cls.__name__}"
        llm.invoke.assert_called_once()
