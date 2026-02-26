"""
Consolidator agent – synthesises specialist analyses into a final report.

Separated from the specialist agents to respect the Single Responsibility
Principle: this module only handles report consolidation logic.
"""

from __future__ import annotations

import datetime as dt
import re

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
            today=dt.date.today().isoformat(),
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
        """Parse the verdict from the LLM consolidation output.

        Strategy (most-specific → least-specific):
        1. Look for the structured verdict pattern the prompt requests:
           ``**Verdict: NOT BUY**`` or ``**Verdict: BUY**``
        2. Fall back to a word-boundary search for ``NOT BUY`` / ``BUY``
           (avoids false positives from words like "buyers" or "buyback").
        3. Default to NOT_BUY (conservative) if nothing matches.
        """
        # --- 1. Structured verdict line (most reliable) ---
        # Matches:  **Verdict: NOT BUY**  |  **Verdict:** NOT BUY
        #           Verdict: NOT BUY      |  **Verdict: BUY**
        structured = re.search(
            r"\*{0,2}Verdict:?\*{0,2}\s*(NOT\s+BUY|BUY)",
            text,
            re.IGNORECASE,
        )
        if structured:
            return (
                Verdict.NOT_BUY
                if "NOT" in structured.group(1).upper()
                else Verdict.BUY
            )

        # --- 2. Word-boundary fallback (handles free-form text) ---
        # Search backwards by scanning all matches and taking the last one.
        not_buy_matches = list(re.finditer(r"\bNOT\s+BUY\b", text, re.IGNORECASE))
        buy_matches = list(re.finditer(r"\bBUY\b", text, re.IGNORECASE))

        if not_buy_matches or buy_matches:
            last_not_buy = not_buy_matches[-1].start() if not_buy_matches else -1
            last_buy = buy_matches[-1].start() if buy_matches else -1

            # If the last "NOT BUY" is at or after the last standalone "BUY",
            # the overall signal is NOT_BUY.  Note: a "NOT BUY" match also
            # contains "BUY", so last_buy >= last_not_buy is common —
            # we need to check whether the last "BUY" *is* part of a "NOT BUY".
            if last_not_buy != -1:
                # Check if the last BUY match is inside the last NOT BUY match
                last_not_buy_end = not_buy_matches[-1].end()
                if last_buy <= last_not_buy_end:
                    return Verdict.NOT_BUY
                # There's a standalone BUY after the last NOT BUY
                return Verdict.BUY

            if last_buy != -1:
                return Verdict.BUY

        # --- 3. Conservative default ---
        return Verdict.NOT_BUY
