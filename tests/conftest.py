"""
Shared test fixtures and helpers.

Provides:
- LangSmith tracing suppression for test sessions
- Mock LLM factories (plain & structured-output)
- Trajectory-tracking mock LLM for verifying agent step sequences
- Sample domain data fixtures
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage

# ---------------------------------------------------------------------------
# Disable LangSmith tracing for the entire test session so mocked
# LangGraph runs don't show up as real traces in the dashboard.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True, scope="session")
def _disable_langsmith_tracing():
    """Turn off LangSmith / LangChain tracing during tests."""
    env_overrides = {
        "LANGCHAIN_TRACING_V2": "false",
        "LANGSMITH_TRACING": "false",
    }
    old_values = {k: os.environ.get(k) for k in env_overrides}
    os.environ.update(env_overrides)
    yield
    for k, v in old_values.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


from ph_stocks_advisor.data.models import (  # noqa: E402
    AdvisorState,
    ControversyAnalysis,
    ControversyInfo,
    DividendAnalysis,
    DividendInfo,
    FairValueEstimate,
    MovementAnalysis,
    PriceAnalysis,
    PriceMovement,
    SentimentAnalysis,
    SentimentInfo,
    StockPrice,
    TrendDirection,
    ValuationAnalysis,
)

# ---------------------------------------------------------------------------
# Mock LLM that returns canned responses
# ---------------------------------------------------------------------------


def make_mock_llm(response_text: str = "Mock analysis.") -> MagicMock:
    """Return a MagicMock that behaves like a BaseChatModel.

    The mock does NOT support ``with_structured_output`` — calling it
    raises ``NotImplementedError`` so the consolidator falls back to
    regex-based verdict extraction.

    ``bind_tools`` returns the same mock so tool-calling agents
    go through the standard invoke path and receive an ``AIMessage``
    (whose ``tool_calls`` defaults to ``[]``, so no tools are invoked).
    """
    llm = MagicMock()
    llm.invoke.return_value = AIMessage(content=response_text)
    llm.with_structured_output.side_effect = NotImplementedError("mock LLM does not support structured output")
    llm.bind_tools.return_value = llm
    return llm


def make_structured_mock_llm(structured_response: Any) -> MagicMock:
    """Return a MagicMock whose ``with_structured_output`` chain returns
    *structured_response* directly.

    Use this to test the structured-output (primary) path of the
    consolidator without hitting a real LLM.
    """
    inner = MagicMock()
    inner.invoke.return_value = structured_response

    llm = MagicMock()
    llm.with_structured_output.return_value = inner
    return llm


# ---------------------------------------------------------------------------
# In-process MCP stub
#
# Production code requires a real MCP server (no in-process fallback). Tests
# must not spin one up, so this autouse fixture replaces ``get_client`` in
# both ``mcp_client`` and ``tools`` with a stub that dispatches each tool
# name to the corresponding in-process service. Tests that patch
# ``ph_stocks_advisor.data.services.*`` therefore keep working unchanged.
#
# Tests that need to verify the *real* MCP-required behaviour (i.e. that
# ``get_client()`` raises when the URL is missing) should mark themselves
# with ``@pytest.mark.no_mcp_stub`` to opt out.
# ---------------------------------------------------------------------------


class _InProcessStubClient:
    """Test stub that runs the same in-process services the MCP server uses."""

    @staticmethod
    def call(tool_name: str, arguments: dict[str, Any]) -> Any:
        from ph_stocks_advisor.data.clients.dragonfi import validate_pse_symbol
        from ph_stocks_advisor.data.services import (
            controversy as _controversy,
        )
        from ph_stocks_advisor.data.services import (
            dividend as _dividend,
        )
        from ph_stocks_advisor.data.services import (
            movement as _movement,
        )
        from ph_stocks_advisor.data.services import (
            price as _price,
        )
        from ph_stocks_advisor.data.services import (
            sentiment as _sentiment,
        )
        from ph_stocks_advisor.data.services import (
            valuation as _valuation,
        )

        symbol = arguments["symbol"]
        if tool_name == "validate_symbol":
            return validate_pse_symbol(symbol)
        if tool_name == "get_stock_price":
            return _price.fetch_stock_price(symbol).model_dump()
        if tool_name == "get_dividend_info":
            return _dividend.fetch_dividend_info(symbol).model_dump()
        if tool_name == "get_price_movement":
            return _movement.fetch_price_movement(symbol).model_dump()
        if tool_name == "get_fair_value":
            return _valuation.fetch_fair_value(symbol).model_dump()
        if tool_name == "get_controversy_info":
            return _controversy.fetch_controversy_info(symbol).model_dump()
        if tool_name == "get_sentiment_info":
            return _sentiment.fetch_sentiment_info(symbol).model_dump()
        raise KeyError(f"Unknown MCP tool: {tool_name!r}")


@pytest.fixture(autouse=True)
def _stub_mcp_client(request, monkeypatch):
    """Auto-replace ``get_client`` with the in-process stub for every test."""
    if request.node.get_closest_marker("no_mcp_stub"):
        return

    from ph_stocks_advisor.data import mcp_client as _mcp
    from ph_stocks_advisor.data import tools as _tools

    stub = _InProcessStubClient()
    monkeypatch.setattr(_mcp, "get_client", lambda url=None: stub)
    monkeypatch.setattr(_tools, "get_client", lambda url=None: stub)


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "no_mcp_stub: do not auto-stub the MCP client (use the real get_client).",
    )


# ---------------------------------------------------------------------------
# Sample domain data fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_stock_price() -> StockPrice:
    return StockPrice(
        symbol="TEL",
        current_price=1250.0,
        currency="PHP",
        fifty_two_week_high=1400.0,
        fifty_two_week_low=1100.0,
        previous_close=1245.0,
    )


@pytest.fixture
def sample_dividend_info() -> DividendInfo:
    return DividendInfo(
        symbol="TEL",
        dividend_rate=75.0,
        dividend_yield=0.06,
        payout_ratio=0.65,
        ex_dividend_date="2025-09-15",
        five_year_avg_yield=5.5,
    )


@pytest.fixture
def sample_price_movement() -> PriceMovement:
    return PriceMovement(
        symbol="TEL",
        year_start_price=1100.0,
        year_end_price=1250.0,
        year_change_pct=13.64,
        max_price=1400.0,
        min_price=1050.0,
        volatility=1.82,
        trend=TrendDirection.UPTREND,
        monthly_prices=[1100, 1120, 1150, 1180, 1200, 1220, 1250, 1280, 1300, 1350, 1380, 1250],
    )


@pytest.fixture
def sample_fair_value() -> FairValueEstimate:
    return FairValueEstimate(
        symbol="TEL",
        current_price=1250.0,
        book_value=800.0,
        pe_ratio=12.5,
        pb_ratio=1.56,
        peg_ratio=1.1,
        forward_pe=11.0,
        estimated_fair_value=1400.0,
        discount_pct=10.71,
    )


@pytest.fixture
def sample_controversy_info() -> ControversyInfo:
    return ControversyInfo(
        symbol="TEL",
        sudden_spikes=["2025-06-10: spike up of 7.2%"],
        risk_factors=["High daily volatility (std > 3%)"],
        recent_news_summary="No automated news feed configured.",
    )


@pytest.fixture
def sample_sentiment_info() -> SentimentInfo:
    return SentimentInfo(
        symbol="TEL",
        global_events_news="No major global events impacting PH market.",
        sector="Services",
    )


@pytest.fixture
def sample_advisor_state(
    sample_stock_price: StockPrice,
    sample_dividend_info: DividendInfo,
    sample_price_movement: PriceMovement,
    sample_fair_value: FairValueEstimate,
    sample_controversy_info: ControversyInfo,
    sample_sentiment_info: SentimentInfo,
) -> AdvisorState:
    return AdvisorState(
        symbol="TEL",
        price_analysis=PriceAnalysis(data=sample_stock_price, analysis="Price looks healthy."),
        dividend_analysis=DividendAnalysis(data=sample_dividend_info, analysis="Dividends are good."),
        movement_analysis=MovementAnalysis(data=sample_price_movement, analysis="Trending up."),
        valuation_analysis=ValuationAnalysis(data=sample_fair_value, analysis="Undervalued."),
        controversy_analysis=ControversyAnalysis(data=sample_controversy_info, analysis="Minor risk."),
        sentiment_analysis=SentimentAnalysis(data=sample_sentiment_info, analysis="Neutral global outlook."),
    )


# ---------------------------------------------------------------------------
# Trajectory-tracking mock LLM  (verifies the *steps* the agent took)
# ---------------------------------------------------------------------------


class TrajectoryTracker:
    """Records every LLM invocation and tool call for trajectory testing.

    Usage::

        tracker = TrajectoryTracker("Mock analysis.")
        agent = PriceAgent(tracker.llm)
        agent.run("TEL")
        assert tracker.was_invoked
        assert tracker.call_count == 1
        # Inspect the prompt that was sent
        assert "TEL" in tracker.prompts[0]
    """

    def __init__(self, response_text: str = "Mock analysis.") -> None:
        self.prompts: list[str] = []
        self.tool_calls: list[str] = []
        self._response_text = response_text
        self.llm = self._build_llm()

    @property
    def was_invoked(self) -> bool:
        return len(self.prompts) > 0

    @property
    def call_count(self) -> int:
        return len(self.prompts)

    def _build_llm(self) -> MagicMock:
        llm = MagicMock()
        llm.invoke.side_effect = self._record_invoke
        llm.with_structured_output.side_effect = NotImplementedError("mock LLM does not support structured output")
        llm.bind_tools.return_value = llm
        return llm

    def _record_invoke(self, messages: Any, **kwargs: Any) -> AIMessage:
        if isinstance(messages, list):
            for msg in messages:
                content = getattr(msg, "content", str(msg))
                self.prompts.append(content)
        else:
            self.prompts.append(str(messages))
        return AIMessage(content=self._response_text)


def make_trajectory_tracker(response_text: str = "Mock analysis.") -> TrajectoryTracker:
    """Factory for trajectory-tracking mocks."""
    return TrajectoryTracker(response_text)
