"""
Portfolio agent – generates personalised analysis for an elevated user's
stock holding.

Single Responsibility: takes an existing stock report and the user's
position data, then produces a tailored advisory note covering hold /
accumulate / trim recommendations.

Dependency Inversion: depends on the ``BaseChatModel`` abstraction
injected at construction time.
"""

from __future__ import annotations

import logging

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage

from ph_stocks_advisor.agents.prompts import PORTFOLIO_ANALYSIS_PROMPT
from ph_stocks_advisor.infra.config import get_today

logger = logging.getLogger(__name__)


class PortfolioAgent:
    """Produces a personalised portfolio advisory note for a single holding."""

    def __init__(self, llm: BaseChatModel) -> None:
        self._llm = llm

    def run(
        self,
        *,
        symbol: str,
        shares: float,
        avg_cost: float,
        current_price: float,
        base_report: str,
        sentiment_context: str = "",
    ) -> str:
        """Generate the portfolio-aware analysis text.

        Parameters
        ----------
        symbol : str
            PSE ticker symbol.
        shares : float
            Number of shares the user holds.
        avg_cost : float
            Average cost per share.
        current_price : float
            Live / latest price of the stock.
        base_report : str
            The full text of the latest stock analysis report.
        sentiment_context : str
            Global events / macro-sentiment analysis text to provide
            broader market context for the advisory.

        Returns
        -------
        str
            The personalised advisory note (markdown-formatted).
        """
        total_cost = shares * avg_cost
        market_value = shares * current_price
        unrealised_pl = market_value - total_cost
        unrealised_pl_pct = (unrealised_pl / total_cost * 100) if total_cost else 0.0

        prompt = PORTFOLIO_ANALYSIS_PROMPT.format(
            today=get_today().isoformat(),
            symbol=symbol,
            shares=shares,
            avg_cost=avg_cost,
            total_cost=total_cost,
            current_price=current_price,
            unrealised_pl=unrealised_pl,
            unrealised_pl_pct=unrealised_pl_pct,
            base_report=base_report,
            sentiment_context=sentiment_context or "No global events context available.",
        )

        response = self._llm.invoke([HumanMessage(content=prompt)])
        return str(response.content)
