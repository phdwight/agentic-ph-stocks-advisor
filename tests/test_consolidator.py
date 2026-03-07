"""Tests for the ConsolidatorAgent.

Verifies both the primary structured-output path (LLM returns a typed
``ConsolidationResponse``) and the regex-fallback path (for LLMs that
don't support structured output).

Canned LLM responses live in ``tests/dummy_responses.py``.
"""

from __future__ import annotations

import pytest

from tests.conftest import make_mock_llm, make_structured_mock_llm
from tests.dummy_responses import (
    CONSOLIDATOR_BUY_RESPONSE,
    CONSOLIDATOR_NOT_BUY_RESPONSE,
)
from ph_stocks_advisor.agents.consolidator import ConsolidatorAgent
from ph_stocks_advisor.data.models import (
    AdvisorState,
    ConsolidationResponse,
    Verdict,
)


# ---------------------------------------------------------------------------
# Tests for the structured-output (primary) path
# ---------------------------------------------------------------------------


class TestConsolidatorStructuredOutput:
    """Verify that the agent uses with_structured_output when available."""

    def test_buy_via_structured_output(self, sample_advisor_state: AdvisorState):
        response = ConsolidationResponse(
            verdict=Verdict.BUY,
            justification="Strong fundamentals and undervalued.",
            summary=CONSOLIDATOR_BUY_RESPONSE,
        )
        llm = make_structured_mock_llm(response)
        agent = ConsolidatorAgent(llm)
        report = agent.run(sample_advisor_state)

        assert report.symbol == "TEL"
        assert report.verdict == Verdict.BUY
        assert "solid investment" in report.summary
        # Verify with_structured_output was called with the right model
        llm.with_structured_output.assert_called_once_with(ConsolidationResponse)
        # Verify sections are populated from state
        assert report.price_section == "Price looks healthy."
        assert report.dividend_section == "Dividends are good."
        assert report.movement_section == "Trending up."
        assert report.valuation_section == "Undervalued."
        assert report.controversy_section == "Minor risk."

    def test_not_buy_via_structured_output(self, sample_advisor_state: AdvisorState):
        response = ConsolidationResponse(
            verdict=Verdict.NOT_BUY,
            justification="Overvalued and high risk.",
            summary=CONSOLIDATOR_NOT_BUY_RESPONSE,
        )
        llm = make_structured_mock_llm(response)
        agent = ConsolidatorAgent(llm)
        report = agent.run(sample_advisor_state)

        assert report.verdict == Verdict.NOT_BUY



# ---------------------------------------------------------------------------
# Tests for the regex-fallback path
# ---------------------------------------------------------------------------


class TestConsolidatorRegexFallback:
    """Verify fallback when with_structured_output raises."""

    @pytest.mark.parametrize("response,expected_verdict,summary_substr", [
        (CONSOLIDATOR_BUY_RESPONSE, Verdict.BUY, "solid"),
        (CONSOLIDATOR_NOT_BUY_RESPONSE, Verdict.NOT_BUY, None),
    ], ids=["buy", "not-buy"])
    def test_run_returns_correct_verdict(
        self, sample_advisor_state: AdvisorState, response, expected_verdict, summary_substr,
    ):
        llm = make_mock_llm(response)
        agent = ConsolidatorAgent(llm)
        report = agent.run(sample_advisor_state)
        assert report.symbol == "TEL"
        assert report.verdict == expected_verdict
        if summary_substr:
            assert summary_substr in report.summary


# ---------------------------------------------------------------------------
# Tests for the static _extract_verdict helper (regex engine)
# ---------------------------------------------------------------------------


class TestExtractVerdict:
    @pytest.mark.parametrize("text,expected", [
        ("Verdict: BUY", Verdict.BUY),
        ("Verdict: NOT BUY", Verdict.NOT_BUY),
        ("**Verdict: BUY**", Verdict.BUY),
        ("**Verdict: NOT BUY**", Verdict.NOT_BUY),
        ("no verdict here", Verdict.NOT_BUY),
        ("Analysis complete. Overall: BUY", Verdict.BUY),
        ("Analysis complete. Overall: NOT BUY", Verdict.NOT_BUY),
        ("**verdict: not buy**", Verdict.NOT_BUY),
        ("**Verdict: Buy**", Verdict.BUY),
    ])
    def test_basic_verdict_extraction(self, text, expected):
        assert ConsolidatorAgent._extract_verdict(text) == expected

    def test_not_buy_takes_precedence(self):
        text = "You should BUY only if... Verdict: NOT BUY"
        assert ConsolidatorAgent._extract_verdict(text) == Verdict.NOT_BUY

    def test_not_buy_with_buyers_after_verdict(self):
        """Regression: 'buyers' after **Verdict: NOT BUY** must not flip to BUY."""
        text = (
            "**Verdict: NOT BUY**\n"
            "At P43.45, AREIT looks fundamentally solid for dividends but is "
            "already near fair value and near resistance, so risk-to-reward "
            "for new buyers is not compelling unless you get a better entry price."
        )
        assert ConsolidatorAgent._extract_verdict(text) == Verdict.NOT_BUY

    def test_not_buy_with_buyback_after_verdict(self):
        """'buyback' should not count as a standalone BUY."""
        text = "**Verdict: NOT BUY**\nThe company announced a share buyback programme."
        assert ConsolidatorAgent._extract_verdict(text) == Verdict.NOT_BUY

    def test_buy_verdict_with_not_buy_mentioned_earlier(self):
        text = "Some analysts said NOT BUY last month.\n**Verdict: BUY**\nFundamentals improved."
        assert ConsolidatorAgent._extract_verdict(text) == Verdict.BUY

    def test_freeform_buy_at_end(self):
        text = "Analysis complete. Overall: BUY"
        assert ConsolidatorAgent._extract_verdict(text) == Verdict.BUY

    def test_freeform_not_buy_at_end(self):
        text = "Analysis complete. Overall: NOT BUY"
        assert ConsolidatorAgent._extract_verdict(text) == Verdict.NOT_BUY

    def test_case_insensitive(self):
        assert ConsolidatorAgent._extract_verdict("**verdict: not buy**") == Verdict.NOT_BUY
        assert ConsolidatorAgent._extract_verdict("**Verdict: Buy**") == Verdict.BUY
