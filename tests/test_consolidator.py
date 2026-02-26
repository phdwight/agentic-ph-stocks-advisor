"""
Tests for the ConsolidatorAgent.
"""

from __future__ import annotations

from tests.conftest import make_mock_llm
from ph_stocks_advisor.agents.consolidator import ConsolidatorAgent
from ph_stocks_advisor.data.models import AdvisorState, Verdict


class TestConsolidatorAgent:
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
