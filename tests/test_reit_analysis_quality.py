"""REIT analysis-quality guards (external-review remediations, 2026-07-22).

1. Cash-based dividend coverage (dividends ÷ FCF, an FFO proxy) is computed
   deterministically in the service — the LLM judges, never does arithmetic.
2. The sentiment payload carries ``is_reit`` and BSP-rate context explicitly
   (REITs are bond proxies; the flag is never inferred by the LLM).
3. Prompt contracts: the payout carve-out is capped, precision must match
   confidence, and unassessed property metrics are disclosed.
"""

from __future__ import annotations

from unittest.mock import patch

from ph_stocks_advisor.data.models import DividendInfo, SentimentInfo

_PROFILE = {
    "price": 37.25,
    "isREIT": True,
    "dividendYield": 5.5,
    "sharesOutstanding": 1_000_000,
}


# ---------------------------------------------------------------------------
# 1. Cash-based coverage (dividends / FCF)
# ---------------------------------------------------------------------------


def _dividend_info_with(fcf_trend, income_trend=None):
    from ph_stocks_advisor.data.services import dividend as div_mod

    with (
        patch.object(div_mod, "fetch_stock_profile", return_value=dict(_PROFILE)),
        patch.object(
            div_mod,
            "fetch_annual_income_trends",
            return_value={"net_income": income_trend or {"2024": 2_000_000.0}, "revenue": {}},
        ),
        patch.object(div_mod, "fetch_annual_cashflow_trends", return_value={"fcf": fcf_trend}),
        patch.object(div_mod, "fetch_recent_dividend_declarations", return_value=[]),
        patch.object(div_mod, "fetch_company_dividend_announcements", return_value=[]),
    ):
        return div_mod.fetch_dividend_info("AREIT")


def test_fcf_payout_ratio_computed_from_latest_fcf():
    # dividend_rate is service-rounded (4dp); ratio uses the LATEST FCF year
    # (2024 = 1.5M, not 2023 = 3M) -> > 1.0: dividends exceed cash generated.
    info = _dividend_info_with({"2023": 3_000_000.0, "2024": 1_500_000.0})
    assert info.fcf_payout_ratio == round((info.dividend_rate * 1_000_000) / 1_500_000, 4)
    assert info.fcf_payout_ratio > 1.0


def test_fcf_payout_ratio_zero_when_fcf_missing_or_negative():
    assert _dividend_info_with({}).fcf_payout_ratio == 0.0
    assert _dividend_info_with({"2024": -500_000.0}).fcf_payout_ratio == 0.0


def test_fcf_payout_ratio_serialised_into_agent_payload():
    assert '"fcf_payout_ratio"' in DividendInfo(symbol="X").model_dump_json()


# ---------------------------------------------------------------------------
# 2. Sentiment payload: is_reit + BSP rate reach the agent
# ---------------------------------------------------------------------------


def test_sentiment_payload_carries_is_reit_and_bsp_rate():
    assert '"is_reit"' in SentimentInfo(symbol="X").model_dump_json()
    assert '"bsp_rate"' in SentimentInfo(symbol="X").model_dump_json()


def test_sentiment_service_populates_reit_flag_and_rate():
    from ph_stocks_advisor.data.services import sentiment as sent_mod

    with (
        patch(
            "ph_stocks_advisor.data.clients.dragonfi.fetch_stock_profile",
            return_value=dict(_PROFILE),
        ),
        patch(
            "ph_stocks_advisor.data.clients.tavily_search.search_global_events",
            return_value="news",
        ),
        patch(
            "ph_stocks_advisor.data.clients.tavily_search.search_bsp_rate",
            return_value="BSP policy rate at X% ...",
        ),
    ):
        info = sent_mod.fetch_sentiment_info("AREIT")
    assert info.is_reit is True
    assert "BSP" in info.bsp_rate


# ---------------------------------------------------------------------------
# 3. Prompt contracts
# ---------------------------------------------------------------------------


def test_dividend_prompt_caps_the_payout_carveout_and_uses_cash_coverage():
    from ph_stocks_advisor.agents.prompts import DIVIDEND_ANALYSIS_PROMPT as p

    assert "fcf_payout_ratio" in p
    assert "NET-INCOME-based" in p
    assert "beyond ~110%" in p  # the carve-out must not extend to e.g. 124.6%


def test_valuation_prompt_requires_ranges_when_inputs_incomplete():
    from ph_stocks_advisor.agents.prompts import VALUATION_ANALYSIS_PROMPT as p

    assert "ROUNDED RANGE" in p
    assert "PRECISION MUST MATCH CONFIDENCE" in p


def test_sentiment_prompt_treats_reits_as_bond_proxies():
    from ph_stocks_advisor.agents.prompts import SENTIMENT_ANALYSIS_PROMPT as p

    assert "bsp_rate" in p
    assert "bond prox" in p.lower()


def test_consolidation_prompt_discloses_unassessed_property_metrics():
    from ph_stocks_advisor.agents.prompts import CONSOLIDATION_PROMPT as p

    assert "WALE" in p
    assert "NOT assessed" in p
    assert 'never call a >110% payout "normal and expected"' in p
