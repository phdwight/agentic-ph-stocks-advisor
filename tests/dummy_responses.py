"""
Centralized dummy LLM responses for tests.

All tests that mock the LLM should import canned responses from this module
instead of using ad-hoc inline strings.  This ensures consistency and makes
the expected format easy to update if agent prompts change.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Specialist agent responses
# ---------------------------------------------------------------------------

PRICE_ANALYSIS_RESPONSE = (
    "TEL's current price of ₱1,250.00 sits near the midpoint of its "
    "52-week range (₱1,100 – ₱1,400).  The stock is trading about 11% "
    "below its 52-week high, suggesting moderate upside potential.  "
    "Previous close was ₱1,245.00, indicating positive momentum.\n\n"
    "**Key observations:**\n"
    "- Price is 52-week midpoint — neither overbought nor oversold\n"
    "- Within 11% of 52-week high — still room to run\n"
    "- Stable day-to-day movement (≈0.4% vs previous close)\n"
)

DIVIDEND_ANALYSIS_RESPONSE = (
    "TEL offers an attractive dividend yield of 6.0%, well above the "
    "PSE average.  The annual dividend rate is ₱75.00 per share.\n\n"
    "**Sustainability assessment:**\n"
    "- Net income grew ~153% from 2022 to 2024 (₱2.89B → ₱7.32B PHP)\n"
    "- Free cash flow in 2024: ₱5.95B (positive)\n"
    "- Payout ratio appears elevated at ~221%, which warrants monitoring\n\n"
    "The high payout ratio is partly attributable to large capital "
    "expenditures; on a recurring-income basis the dividend is "
    "better-supported than headline numbers suggest."
)

MOVEMENT_ANALYSIS_RESPONSE = (
    "TEL has been in a sustained uptrend over the past year, gaining "
    "~13.6%.  The stock rose from ₱1,100 to ₱1,250, with a peak of "
    "₱1,400 mid-year.\n\n"
    "**Technical summary:**\n"
    "- Trend: Uptrend (+13.6% YoY)\n"
    "- Max price: ₱1,400 | Min price: ₱1,050\n"
    "- Volatility: 1.82% (moderate)\n"
    "- No significant drawdown events detected\n"
)

VALUATION_ANALYSIS_RESPONSE = (
    "TEL appears undervalued by approximately 10.7%.  The current price "
    "of ₱1,250 is below the Graham Number fair value estimate of ₱1,400.\n\n"
    "**Key ratios:**\n"
    "- P/E Ratio: 12.5\n"
    "- P/B Ratio: 1.56\n"
    "- PEG Ratio: 1.1\n"
    "- Forward P/E: 11.0\n\n"
    "The moderate P/E and PEG near 1.0 suggest the stock is reasonably "
    "valued relative to its earnings growth."
)

CONTROVERSY_ANALYSIS_RESPONSE = (
    "One notable price spike was detected on 2025-06-10 (+7.2%), likely "
    "driven by an earnings surprise.  Overall risk profile is manageable.\n\n"
    "**Risk factors:**\n"
    "- High daily volatility (std > 3%) on select days\n"
    "- No major controversies or litigation identified in recent news\n"
    "- No automated news feed configured — manual review recommended\n"
)

SENTIMENT_ANALYSIS_RESPONSE = (
    "Global sentiment is cautiously Neutral for TEL and the broader PSE.\n\n"
    "**Geopolitical risks:**\n"
    "- South China Sea tensions remain elevated but have not escalated\n"
    "- No direct impact on TEL's telco operations observed\n\n"
    "**Health risks:**\n"
    "- No active pandemic threat; COVID-19 is in endemic phase\n\n"
    "**Global economic shifts:**\n"
    "- US Fed holding rates steady benefits emerging-market inflows\n"
    "- PHP/USD stable around ₱56-57 range\n\n"
    "**Net sentiment: Neutral** — no major global headwinds or tailwinds.\n"
)


# ---------------------------------------------------------------------------
# Consolidator responses — structured output path
# ---------------------------------------------------------------------------

CONSOLIDATOR_BUY_RESPONSE = (
    "Executive summary: TEL is a solid investment with strong dividend "
    "yield, positive earnings momentum, and a price that sits below "
    "fair value.  The stock's uptrend and manageable risk profile "
    "support a BUY recommendation.\n\n"
    "**Verdict: BUY**\n\n"
    "Justification: Good dividends (6% yield), attractive valuation "
    "(10.7% discount to fair value), and sustainable earnings growth."
)

CONSOLIDATOR_NOT_BUY_RESPONSE = (
    "Executive summary: TEL is overpriced relative to its current "
    "earnings trajectory.  While dividends remain attractive, the "
    "elevated payout ratio and proximity to 52-week highs limit "
    "upside.\n\n"
    "**Verdict: NOT BUY**\n\n"
    "Justification: Payout ratio unsustainable, price near resistance, "
    "and limited margin of safety."
)


# ---------------------------------------------------------------------------
# Map: agent class name → dummy response text
# ---------------------------------------------------------------------------

AGENT_RESPONSES: dict[str, str] = {
    "PriceAgent": PRICE_ANALYSIS_RESPONSE,
    "DividendAgent": DIVIDEND_ANALYSIS_RESPONSE,
    "MovementAgent": MOVEMENT_ANALYSIS_RESPONSE,
    "ValuationAgent": VALUATION_ANALYSIS_RESPONSE,
    "ControversyAgent": CONTROVERSY_ANALYSIS_RESPONSE,
    "SentimentAgent": SENTIMENT_ANALYSIS_RESPONSE,
    "ConsolidatorAgent": CONSOLIDATOR_BUY_RESPONSE,
}
