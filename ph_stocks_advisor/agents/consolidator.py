"""
Consolidator agent – synthesises specialist analyses into a final report.

Separated from the specialist agents to respect the Single Responsibility
Principle: this module only handles report consolidation logic.
"""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage

from ph_stocks_advisor.data.models import (
    AdvisorState,
    FinalReport,
    Verdict,
)
from ph_stocks_advisor.agents.prompts import CONSOLIDATION_PROMPT


class ConsolidatorAgent:
    """Merges all specialist analyses into a single investor-friendly report."""

    def __init__(self, llm: BaseChatModel) -> None:
        self._llm = llm

    def run(self, state: AdvisorState) -> FinalReport:
        prompt = CONSOLIDATION_PROMPT.format(
            symbol=state.symbol,
            price_analysis=state.price_analysis.analysis if state.price_analysis else "N/A",
            dividend_analysis=state.dividend_analysis.analysis if state.dividend_analysis else "N/A",
            movement_analysis=state.movement_analysis.analysis if state.movement_analysis else "N/A",
            valuation_analysis=state.valuation_analysis.analysis if state.valuation_analysis else "N/A",
            controversy_analysis=state.controversy_analysis.analysis if state.controversy_analysis else "N/A",
        )
        response = self._llm.invoke([HumanMessage(content=prompt)])
        content = str(response.content)

        # Determine verdict from LLM output
        verdict = self._extract_verdict(content)

        return FinalReport(
            symbol=state.symbol,
            verdict=verdict,
            summary=content,
            price_section=state.price_analysis.analysis if state.price_analysis else "",
            dividend_section=state.dividend_analysis.analysis if state.dividend_analysis else "",
            movement_section=state.movement_analysis.analysis if state.movement_analysis else "",
            valuation_section=state.valuation_analysis.analysis if state.valuation_analysis else "",
            controversy_section=state.controversy_analysis.analysis if state.controversy_analysis else "",
        )

    @staticmethod
    def _extract_verdict(text: str) -> Verdict:
        """Parse the verdict from the LLM consolidation output."""
        upper = text.upper()
        # Search backwards – the verdict is typically at the end
        not_buy_pos = upper.rfind("NOT BUY")
        buy_pos = upper.rfind("BUY")
        if not_buy_pos != -1 and not_buy_pos >= buy_pos - 4:
            return Verdict.NOT_BUY
        if buy_pos != -1:
            return Verdict.BUY
        # Default conservative stance if parsing fails
        return Verdict.NOT_BUY
