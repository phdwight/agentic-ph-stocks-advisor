"""Tests for the ConsolidatorAgent.

Verifies both the primary structured-output path (LLM returns a typed
``ConsolidationResponse``) and the regex-fallback path (for LLMs that
don't support structured output).
"""

from __future__ import annotations

from tests.conftest import make_mock_llm, make_structured_mock_llm
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
            summary="Executive summary: TEL is a solid stock.",
        )
        llm = make_structured_mock_llm(response)
        agent = ConsolidatorAgent(llm)
        report = agent.run(sample_advisor_state)

        assert report.symbol == "TEL"
        assert report.verdict == Verdict.BUY
        assert "solid stock" in report.summary
        # Verify with_structured_output was called with the right model
        llm.with_structured_output.assert_called_once_with(ConsolidationResponse)

    def test_not_buy_via_structured_output(self, sample_advisor_state: AdvisorState):
        response = ConsolidationResponse(
            verdict=Verdict.NOT_BUY,
            justification="Overvalued and high risk.",
            summary="Executive summary: TEL is too expensive.",
        )
        llm = make_structured_mock_llm(response)
        agent = ConsolidatorAgent(llm)
        report = agent.run(sample_advisor_state)

        assert report.verdict == Verdict.NOT_BUY

    def test_sections_populated_from_state(self, sample_advisor_state: AdvisorState):
        response = ConsolidationResponse(
            verdict=Verdict.BUY,
            justification="Good.",
            summary="Report.",
        )
        llm = make_structured_mock_llm(response)
        agent = ConsolidatorAgent(llm)
        report = agent.run(sample_advisor_state)

        assert report.price_section == "Price looks healthy."
        assert report.dividend_section == "Dividends are good."
        assert report.movement_section == "Trending up."
        assert report.valuation_section == "Undervalued."
        assert report.controversy_section == "Minor risk."


# ---------------------------------------------------------------------------
# Tests for the regex-fallback path
# ---------------------------------------------------------------------------


class TestConsolidatorRegexFallback:
    """Verify fallback when with_structured_output raises."""

    def test_run_returns_final_report(self, sample_advisor_state: AdvisorState):
        llm = make_mock_llm(
            "Executive summary: TEL is a solid stock.\n\n"
            "**Verdict: BUY**\n"
            "Justification: Good dividends and undervalued."
        )
        agent = ConsolidatorAgent(llm)
        report = agent.run(sample_advisor_state)
        assert report.symbol == "TEL"
        assert report.verdict == Verdict.BUY
        assert "solid stock" in report.summary

    def test_not_buy_verdict(self, sample_advisor_state: AdvisorState):
        llm = make_mock_llm(
            "Executive summary: TEL is overpriced.\n\n"
            "**Verdict: NOT BUY**\n"
            "Justification: Too expensive."
        )
        agent = ConsolidatorAgent(llm)
        report = agent.run(sample_advisor_state)
        assert report.verdict == Verdict.NOT_BUY


# ---------------------------------------------------------------------------
# Tests for the static _extract_verdict helper (regex engine)
# ---------------------------------------------------------------------------


class TestExtractVerdict:
    def test_buy(self):
        assert ConsolidatorAgent._extract_verdict("Verdict: BUY") == Verdict.BUY

    def test_not_buy(self):
        assert ConsolidatorAgent._extract_verdict("Verdict: NOT BUY") == Verdict.NOT_BUY

    def test_not_buy_takes_precedence(self):
        text = "You should BUY only if... Verdict: NOT BUY"
        assert ConsolidatorAgent._extract_verdict(text) == Verdict.NOT_BUY

    def test_defaults_to_not_buy(self):
        assert ConsolidatorAgent._extract_verdict("no verdict here") == Verdict.NOT_BUY

    def test_structured_bold_verdict_buy(self):
        assert ConsolidatorAgent._extract_verdict("**Verdict: BUY**") == Verdict.BUY

    def test_structured_bold_verdict_not_buy(self):
        assert ConsolidatorAgent._extract_verdict("**Verdict: NOT BUY**") == Verdict.NOT_BUY

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
