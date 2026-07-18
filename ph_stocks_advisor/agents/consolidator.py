"""
Consolidator agent – synthesises specialist analyses into a final report.

Separated from the specialist agents to respect the Single Responsibility
Principle: this module only handles report consolidation logic.

Uses ``BaseChatModel.with_structured_output()`` to enforce a typed
``ConsolidationResponse`` from the LLM, eliminating fragile regex-based
verdict parsing.  Falls back to free-form text + regex extraction when
the LLM does not support structured output.
"""

from __future__ import annotations

import logging
import re

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage

from ph_stocks_advisor.agents.prompts import CONSOLIDATION_PROMPT
from ph_stocks_advisor.data.models import (
    AdvisorState,
    ConsolidationResponse,
    FinalReport,
    Verdict,
    score_band,
)
from ph_stocks_advisor.infra.config import get_settings, get_today

logger = logging.getLogger(__name__)

# Score assigned when the LLM provides only a binary verdict (regex
# fallback, or a structured model that omitted the sub-scores): solidly
# inside the BUY / SELL bands without overstating conviction.
_FALLBACK_BUY_SCORE = 75
_FALLBACK_NOT_BUY_SCORE = 25


class ConsolidatorAgent:
    """Merges all specialist analyses into a single investor-friendly report."""

    def __init__(self, llm: BaseChatModel) -> None:
        self._llm = llm

    def run(self, state: AdvisorState) -> FinalReport:
        prompt = CONSOLIDATION_PROMPT.format(
            symbol=state.symbol,
            today=get_today().isoformat(),
            price_analysis=state.price_analysis.analysis if state.price_analysis else "N/A",
            dividend_analysis=state.dividend_analysis.analysis if state.dividend_analysis else "N/A",
            movement_analysis=state.movement_analysis.analysis if state.movement_analysis else "N/A",
            valuation_analysis=state.valuation_analysis.analysis if state.valuation_analysis else "N/A",
            controversy_analysis=state.controversy_analysis.analysis if state.controversy_analysis else "N/A",
            sentiment_analysis=state.sentiment_analysis.analysis if state.sentiment_analysis else "N/A",
        )

        verdict, summary, score = self._invoke_structured(prompt)

        return FinalReport(
            symbol=state.symbol,
            verdict=verdict,
            summary=summary,
            score=score,
            price_section=state.price_analysis.analysis if state.price_analysis else "",
            dividend_section=state.dividend_analysis.analysis if state.dividend_analysis else "",
            movement_section=state.movement_analysis.analysis if state.movement_analysis else "",
            valuation_section=state.valuation_analysis.analysis if state.valuation_analysis else "",
            controversy_section=state.controversy_analysis.analysis if state.controversy_analysis else "",
            sentiment_section=state.sentiment_analysis.analysis if state.sentiment_analysis else "",
        )

    # ------------------------------------------------------------------
    # Structured output (primary) → free-form + regex (fallback)
    # ------------------------------------------------------------------

    def _invoke_structured(self, prompt: str) -> tuple[Verdict, str, int]:
        """Try structured output first; fall back to regex extraction.

        Returns ``(verdict, summary, score)`` regardless of which path
        succeeds. When sub-scores are available the final score is their
        configurable weighted average and the binary verdict is DERIVED
        from it (score >= buy threshold → BUY) so the meter, the band
        label, and the badge can never contradict each other.
        """
        try:
            structured_llm = self._llm.with_structured_output(ConsolidationResponse)
            result: ConsolidationResponse = structured_llm.invoke([HumanMessage(content=prompt)])  # type: ignore[assignment]
            score = self._weighted_score(result)
            if score is None:
                score = _FALLBACK_BUY_SCORE if result.verdict == Verdict.BUY else _FALLBACK_NOT_BUY_SCORE
                verdict = result.verdict
            else:
                verdict = Verdict.BUY if score >= get_settings().buy_score_threshold else Verdict.NOT_BUY
                if verdict != result.verdict:
                    logger.info(
                        "Derived verdict %s (score=%d) overrides LLM verdict %s.",
                        verdict.value,
                        score,
                        result.verdict.value,
                    )
            logger.info(
                "Structured output succeeded — verdict=%s score=%d band=%s",
                verdict.value,
                score,
                score_band(score),
            )
            return verdict, result.summary, score
        except (NotImplementedError, AttributeError, TypeError) as exc:
            logger.info(
                "Structured output not supported (%s); falling back to regex.",
                exc,
            )

        # Fallback: invoke without structured output and parse manually
        response = self._llm.invoke([HumanMessage(content=prompt)])
        content = str(response.content)
        verdict = self._extract_verdict(content)
        score = _FALLBACK_BUY_SCORE if verdict == Verdict.BUY else _FALLBACK_NOT_BUY_SCORE
        return verdict, content, score

    @staticmethod
    def _weighted_score(result: ConsolidationResponse) -> int | None:
        """Weighted average of the per-dimension sub-scores, or ``None``.

        Weights come from Settings (env-tunable) and are normalised by
        their sum, so they need not add to exactly 1. Dimensions the LLM
        omitted are skipped (their weight is excluded); if every
        sub-score is missing the caller falls back to a verdict-derived
        score.
        """
        s = get_settings()
        pairs: list[tuple[int | None, float]] = [
            (result.price_score, s.score_weight_price),
            (result.valuation_score, s.score_weight_valuation),
            (result.dividend_score, s.score_weight_dividend),
            (result.movement_score, s.score_weight_movement),
            (result.controversy_score, s.score_weight_controversy),
            (result.sentiment_score, s.score_weight_sentiment),
        ]
        present = [(v, w) for v, w in pairs if v is not None and w > 0]
        total_weight = sum(w for _, w in present)
        if not present or total_weight <= 0:
            return None
        raw = sum(v * w for v, w in present) / total_weight
        return max(0, min(100, round(raw)))

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
            return Verdict.NOT_BUY if "NOT" in structured.group(1).upper() else Verdict.BUY

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
