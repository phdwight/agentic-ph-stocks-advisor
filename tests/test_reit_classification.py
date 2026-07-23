"""REIT classification must come from data, never from the LLM's guess.

JOH (Jolliville Holdings) was described as a REIT in a live report because
``FairValueEstimate`` carried no ``is_reit`` field while the valuation prompt
told the model to "apply when ``is_reit`` is true" — the flag never reached
the payload, so the model inferred it from the company profile.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from ph_stocks_advisor.data.models import DividendInfo, FairValueEstimate

_REIT_PROFILE = {"price": 37.2, "isREIT": True, "dividendYield": 6.5, "sharesOutstanding": 1_000_000}
_NON_REIT_PROFILE = {"price": 3.01, "isREIT": False, "dividendYield": 0}


@pytest.mark.parametrize("is_reit", [True, False])
def test_fair_value_carries_reit_flag(is_reit):
    """The valuation payload must state REIT status explicitly."""
    from ph_stocks_advisor.data.services import valuation as val_mod

    profile = dict(_REIT_PROFILE if is_reit else _NON_REIT_PROFILE)
    with (
        patch.object(val_mod, "fetch_stock_profile", return_value=profile),
        patch.object(val_mod, "fetch_security_valuation", return_value={}),
    ):
        assert val_mod.fetch_fair_value("SYM").is_reit is is_reit


def test_fair_value_keeps_reit_flag_when_valuation_data_missing():
    """Even the no-data fallback must not silently claim non-REIT."""
    from ph_stocks_advisor.data.services import valuation as val_mod

    with (
        patch.object(val_mod, "fetch_stock_profile", return_value={"isREIT": True, "price": 0}),
        patch.object(val_mod, "fetch_security_valuation", return_value={}),
        patch("ph_stocks_advisor.data.clients.pse_edge.fetch_stock_snapshot", return_value=None),
        patch("ph_stocks_advisor.data.clients.pse_edge.fetch_annual_financials", return_value=None),
    ):
        assert val_mod.fetch_fair_value("SYM").is_reit is True


def test_dividend_fallback_keeps_reit_flag():
    """A REIT with no dividend yield data must still report is_reit=True."""
    from ph_stocks_advisor.data.services import dividend as div_mod

    with patch.object(div_mod, "fetch_stock_profile", return_value={"isREIT": True, "dividendYield": 0}):
        assert div_mod.fetch_dividend_info("SYM").is_reit is True


def test_reit_flag_is_serialised_into_the_agent_prompt():
    """Agents receive the flag because prompts embed model_dump_json()."""
    assert '"is_reit"' in FairValueEstimate(symbol="SYM").model_dump_json()
    assert '"is_reit"' in DividendInfo(symbol="SYM").model_dump_json()


def test_valuation_prompt_states_the_non_reit_case():
    """The prompt must forbid inferring REIT status, not just describe the
    positive case — the original wording only said 'apply when is_reit is
    true', leaving the false case to the model's judgement."""
    from ph_stocks_advisor.agents.prompts import VALUATION_ANALYSIS_PROMPT

    text = VALUATION_ANALYSIS_PROMPT.lower()
    assert "never infer it" in text
    assert "is **false**" in text
