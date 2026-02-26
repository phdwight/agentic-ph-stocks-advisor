"""
Dividend data service — fetches dividend metrics for PSE stocks.

Single Responsibility: only handles dividend data retrieval
and sustainability analysis.
"""

from __future__ import annotations

import logging
from typing import Any

from ph_stocks_advisor.data.dragonfi import (
    fetch_annual_cashflow_trends,
    fetch_annual_income_trends,
    fetch_stock_profile,
)
from ph_stocks_advisor.data.models import DividendInfo
from ph_stocks_advisor.data.tavily_search import search_dividend_news

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_sustainability_note(
    *,
    is_reit: bool,
    payout_ratio: float,
    net_income_trend: dict[str, float],
    fcf_trend: dict[str, float],
) -> str:
    """Generate a human-readable sustainability assessment."""
    parts: list[str] = []

    if is_reit:
        parts.append(
            "This is a Philippine REIT, legally required to distribute "
            "at least 90% of its distributable income as dividends."
        )

    if len(net_income_trend) >= 3:
        years_sorted = sorted(net_income_trend.keys())
        first_ni = net_income_trend[years_sorted[0]]
        last_ni = net_income_trend[years_sorted[-1]]
        if first_ni > 0 and last_ni > first_ni:
            growth = ((last_ni - first_ni) / first_ni) * 100
            parts.append(
                f"Net income grew ~{growth:.0f}% from {years_sorted[0]} to "
                f"{years_sorted[-1]} ({first_ni/1e9:.2f}B → {last_ni/1e9:.2f}B PHP), "
                "supporting the dividend."
            )
        elif last_ni > 0:
            parts.append(
                f"Net income in {years_sorted[-1]}: {last_ni/1e9:.2f}B PHP."
            )

    if payout_ratio > 0:
        parts.append(f"Estimated payout ratio: {payout_ratio*100:.1f}%.")

    if fcf_trend:
        latest_fcf_year = max(fcf_trend.keys())
        latest_fcf = fcf_trend[latest_fcf_year]
        if latest_fcf > 0:
            parts.append(
                f"Free cash flow in {latest_fcf_year}: {latest_fcf/1e9:.2f}B PHP (positive)."
            )

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_dividend_info(symbol: str) -> DividendInfo:
    """Fetch dividend data for a PSE-listed stock.

    Source: DragonFi (profile + financials). Returns a minimal object when
    dividend data is unavailable.

    Computes:
    - ``dividend_rate`` from yield × price (per-share annual dividend)
    - ``payout_ratio`` from estimated total dividends vs. net income
    - ``is_reit`` flag (Philippine REITs must distribute ≥90% distributable income)
    - ``net_income_trend`` / ``revenue_trend`` / ``free_cash_flow_trend`` — multi-year
    - ``dividend_sustainability_note`` — auto-generated note about sustainability
    """
    symbol = symbol.upper().replace(".PS", "")
    profile = fetch_stock_profile(symbol)

    div_yield_raw = float(profile.get("dividendYield", 0) or 0) if profile else 0.0

    if div_yield_raw > 0 and profile:
        # DragonFi dividend yield is a percentage (e.g. 5.54) → normalise to decimal
        div_yield = div_yield_raw / 100.0 if div_yield_raw > 1 else div_yield_raw

        current_price = float(profile.get("price", 0) or 0)
        shares_outstanding = float(profile.get("sharesOutstanding", 0) or 0)
        is_reit = bool(profile.get("isREIT", False))

        # Compute per-share annual dividend rate from yield × price
        dividend_rate = round(current_price * div_yield, 4) if current_price else 0.0

        # Fetch financial trends
        income_trends = fetch_annual_income_trends(symbol)
        cf_trends = fetch_annual_cashflow_trends(symbol)

        net_income_trend = income_trends.get("net_income") or {}
        revenue_trend = income_trends.get("revenue") or {}
        fcf_trend = cf_trends.get("fcf") or {}

        # Estimate payout ratio: total dividends / net income (latest year)
        payout_ratio = 0.0
        if dividend_rate > 0 and shares_outstanding > 0 and net_income_trend:
            latest_year = max(net_income_trend.keys())
            latest_ni = net_income_trend[latest_year]
            if latest_ni > 0:
                total_dividends = dividend_rate * shares_outstanding
                payout_ratio = round(total_dividends / latest_ni, 4)

        sustainability_note = _build_sustainability_note(
            is_reit=is_reit,
            payout_ratio=payout_ratio,
            net_income_trend=net_income_trend,
            fcf_trend=fcf_trend,
        )

        return DividendInfo(
            symbol=symbol,
            dividend_rate=dividend_rate,
            dividend_yield=div_yield,
            payout_ratio=payout_ratio,
            ex_dividend_date=None,
            five_year_avg_yield=0.0,
            is_reit=is_reit,
            annual_dividend_per_share=dividend_rate,
            net_income_trend=net_income_trend,
            revenue_trend=revenue_trend,
            free_cash_flow_trend=fcf_trend,
            dividend_sustainability_note=sustainability_note,
            recent_dividend_news=search_dividend_news(
                symbol, company_name=str(profile.get("companyName", "")),
            ),
        )

    # No dividend data available from DragonFi
    logger.warning("DragonFi returned no dividend data for %s", symbol)
    return DividendInfo(symbol=symbol)
