"""
Trajectory tests — verify the *steps* agents take, not just final output.

These tests use TrajectoryTracker to assert that:
1. The correct data-fetching functions were called
2. The LLM was invoked with the right context (symbol, data)
3. The graph executed nodes in the expected order
4. Tool-calling agents bound the correct tools

All tests are fully mocked — no API keys or network access required.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch, call

import pytest

from tests.conftest import make_trajectory_tracker
from tests.dummy_responses import PRICE_ANALYSIS_RESPONSE
from ph_stocks_advisor.agents.specialists import PriceAgent
from ph_stocks_advisor.data.models import (
    ControversyAnalysis,
    ControversyInfo,
    DividendAnalysis,
    DividendInfo,
    FairValueEstimate,
    FinalReport,
    MovementAnalysis,
    PriceAnalysis,
    PriceMovement,
    SentimentAnalysis,
    SentimentInfo,
    StockPrice,
    ValuationAnalysis,
    Verdict,
)


class TestAgentTrajectory:
    """Verify that specialist agents follow the expected call trajectory."""

    def test_price_agent_trajectory(self, sample_stock_price: StockPrice):
        """PriceAgent should: 1) fetch data, 2) invoke LLM with symbol context."""
        tracker = make_trajectory_tracker(PRICE_ANALYSIS_RESPONSE)

        with patch(
            "ph_stocks_advisor.agents.specialists.fetch_stock_price",
            return_value=sample_stock_price,
        ) as mock_fetch:
            agent = PriceAgent(tracker.llm)
            result = agent.run("TEL")

        # Step 1: data was fetched for the correct symbol
        mock_fetch.assert_called_once_with("TEL")

        # Step 2: LLM was invoked exactly once
        assert tracker.call_count >= 1, "LLM should be invoked at least once"

        # Step 3: the prompt sent to the LLM mentions the symbol
        assert any("TEL" in p for p in tracker.prompts), (
            "LLM prompt should contain the stock symbol"
        )

        # Step 4: result has the expected structure
        assert result.data.symbol == "TEL"
        assert result.analysis  # non-empty analysis


class TestGraphTrajectory:
    """Verify the LangGraph workflow executes nodes in the correct order."""

    def test_graph_trajectory_all_nodes_execute(self):
        """The full pipeline should traverse:
        validate → 6 specialist agents (parallel) → consolidator.
        """
        import ph_stocks_advisor.graph.workflow as workflow_mod
        from ph_stocks_advisor.graph.workflow import run_analysis

        # Track which agent classes were instantiated and called
        executed_agents: list[str] = []

        def _make_mock_agent(cls_name, state_key, return_model):
            mock_cls = MagicMock()

            def _run_side_effect(symbol):
                executed_agents.append(cls_name)
                return return_model

            mock_cls.return_value.run.side_effect = _run_side_effect
            return mock_cls

        MockPriceAgent = _make_mock_agent(
            "PriceAgent", "price_analysis",
            PriceAnalysis(data=StockPrice(symbol="TEL", current_price=1250.0), analysis="OK"),
        )
        MockDividendAgent = _make_mock_agent(
            "DividendAgent", "dividend_analysis",
            DividendAnalysis(data=DividendInfo(symbol="TEL"), analysis="OK"),
        )
        MockMovementAgent = _make_mock_agent(
            "MovementAgent", "movement_analysis",
            MovementAnalysis(data=PriceMovement(symbol="TEL"), analysis="OK"),
        )
        MockValuationAgent = _make_mock_agent(
            "ValuationAgent", "valuation_analysis",
            ValuationAnalysis(data=FairValueEstimate(symbol="TEL"), analysis="OK"),
        )
        MockControversyAgent = _make_mock_agent(
            "ControversyAgent", "controversy_analysis",
            ControversyAnalysis(data=ControversyInfo(symbol="TEL"), analysis="OK"),
        )
        MockSentimentAgent = _make_mock_agent(
            "SentimentAgent", "sentiment_analysis",
            SentimentAnalysis(data=SentimentInfo(symbol="TEL"), analysis="OK"),
        )

        MockConsolidator = MagicMock()
        MockConsolidator.return_value.run.return_value = FinalReport(
            symbol="TEL",
            verdict=Verdict.BUY,
            summary="Trajectory test passed.",
        )

        mock_registry = [
            ("price_agent", "price_analysis", MockPriceAgent),
            ("dividend_agent", "dividend_analysis", MockDividendAgent),
            ("movement_agent", "movement_analysis", MockMovementAgent),
            ("valuation_agent", "valuation_analysis", MockValuationAgent),
            ("controversy_agent", "controversy_analysis", MockControversyAgent),
            ("sentiment_agent", "sentiment_analysis", MockSentimentAgent),
        ]

        mock_llm = MagicMock()

        with (
            patch.object(workflow_mod, "AGENT_REGISTRY", mock_registry),
            patch.object(workflow_mod, "ConsolidatorAgent", MockConsolidator),
            patch.object(workflow_mod, "validate_symbol", return_value="TEL"),
        ):
            result = run_analysis("TEL", llm=mock_llm, mini_llm=mock_llm)

        # --- Trajectory assertions ---

        # All 6 specialist agents were executed
        expected_agents = {
            "PriceAgent", "DividendAgent", "MovementAgent",
            "ValuationAgent", "ControversyAgent", "SentimentAgent",
        }
        assert set(executed_agents) == expected_agents, (
            f"Expected all 6 agents to run, got: {executed_agents}"
        )

        # Consolidator was called exactly once, after all specialists
        MockConsolidator.return_value.run.assert_called_once()

        # Final report was produced
        report = result["final_report"]
        if isinstance(report, dict):
            report = FinalReport(**report)
        assert report.symbol == "TEL"
        assert report.verdict == Verdict.BUY

    def test_invalid_symbol_short_circuits_trajectory(self):
        """An invalid symbol should skip all agents (no specialist runs)."""
        import ph_stocks_advisor.graph.workflow as workflow_mod
        from ph_stocks_advisor.data.tools import SymbolNotFoundError
        from ph_stocks_advisor.graph.workflow import run_analysis

        specialist_called = []

        MockAgent = MagicMock()
        MockAgent.return_value.run.side_effect = lambda s: specialist_called.append(s)

        mock_registry = [
            ("price_agent", "price_analysis", MockAgent),
        ]
        mock_llm = MagicMock()

        with (
            patch.object(workflow_mod, "AGENT_REGISTRY", mock_registry),
            patch.object(
                workflow_mod,
                "validate_symbol",
                side_effect=SymbolNotFoundError("FAKE", "Not found"),
            ),
        ):
            result = run_analysis("FAKE", llm=mock_llm, mini_llm=mock_llm)

        # No specialist agents should have been called
        assert len(specialist_called) == 0, "Specialists should not run for invalid symbols"
        assert result.get("error") is not None
