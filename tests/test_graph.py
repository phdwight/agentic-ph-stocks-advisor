"""
Tests for the LangGraph workflow construction.

These tests verify graph structure and node wiring.
Integration tests that invoke the full graph with mocked agents are included.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import ph_stocks_advisor.graph.workflow as workflow_mod
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
from ph_stocks_advisor.graph.workflow import AGENT_REGISTRY, _build_graph_impl, run_analysis


class TestBuildGraph:
    def test_graph_compiles(self):
        """The graph should compile without errors when given a mock LLM."""
        mock_llm = MagicMock()
        graph = _build_graph_impl(llm=mock_llm, mini_llm=mock_llm)
        assert graph is not None

    def test_registry_drives_node_creation(self):
        """Every agent in AGENT_REGISTRY should result in a graph node,
        and all expected infrastructure nodes should be present."""
        mock_llm = MagicMock()
        graph = _build_graph_impl(llm=mock_llm, mini_llm=mock_llm)
        node_names = set(graph.get_graph().nodes.keys())
        # Check infrastructure nodes
        for name in ("validate", "consolidator"):
            assert name in node_names
        # Check all registered agent nodes
        for node_name, _key, _cls in AGENT_REGISTRY:
            assert node_name in node_names


class TestRunAnalysisIntegration:
    """Integration test that mocks agent classes and runs the full graph."""

    def test_full_pipeline(self):
        """All agents produce results and the consolidator merges them."""
        # Create mock agent classes
        MockPriceAgent = MagicMock()
        MockPriceAgent.return_value.run.return_value = PriceAnalysis(
            data=StockPrice(symbol="TEL", current_price=1250.0),
            analysis="Price OK.",
        )
        MockDividendAgent = MagicMock()
        MockDividendAgent.return_value.run.return_value = DividendAnalysis(
            data=DividendInfo(symbol="TEL"),
            analysis="Dividend OK.",
        )
        MockMovementAgent = MagicMock()
        MockMovementAgent.return_value.run.return_value = MovementAnalysis(
            data=PriceMovement(symbol="TEL"),
            analysis="Movement OK.",
        )
        MockValuationAgent = MagicMock()
        MockValuationAgent.return_value.run.return_value = ValuationAnalysis(
            data=FairValueEstimate(symbol="TEL"),
            analysis="Valuation OK.",
        )
        MockControversyAgent = MagicMock()
        MockControversyAgent.return_value.run.return_value = ControversyAnalysis(
            data=ControversyInfo(symbol="TEL"),
            analysis="Risk OK.",
        )

        MockSentimentAgent = MagicMock()
        MockSentimentAgent.return_value.run.return_value = SentimentAnalysis(
            data=SentimentInfo(symbol="TEL"),
            analysis="Sentiment OK.",
        )

        MockConsolidator = MagicMock()
        MockConsolidator.return_value.run.return_value = FinalReport(
            symbol="TEL",
            verdict=Verdict.BUY,
            summary="TEL is a solid investment.",
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

        report = result["final_report"]
        if isinstance(report, dict):
            report = FinalReport(**report)

        assert report.symbol == "TEL"
        assert report.verdict == Verdict.BUY
        assert "solid" in report.summary


class TestValidationFailure:
    """Test that an invalid symbol short-circuits the graph."""

    def test_invalid_symbol_returns_error(self):
        from ph_stocks_advisor.data.tools import SymbolNotFoundError

        mock_llm = MagicMock()

        with patch.object(
            workflow_mod,
            "validate_symbol",
            side_effect=SymbolNotFoundError("TEL", "Symbol 'XYZ' not found."),
        ):
            result = run_analysis("XYZ", llm=mock_llm, mini_llm=mock_llm)

        assert result.get("error") is not None
        assert "XYZ" in result["error"]
        assert result.get("final_report") is None


class TestEmptyAgentDataShortCircuit:
    """When a specialist returns empty upstream data, the pipeline must abort."""

    def test_empty_data_aborts_and_skips_consolidator(self):
        """An empty PriceAgent payload must surface an error and skip consolidation."""
        from ph_stocks_advisor.agents.specialists import EmptyAgentDataError

        # PriceAgent fails with empty-data; the other specialists succeed.
        FailingPriceAgent = MagicMock()
        FailingPriceAgent.return_value.run.side_effect = EmptyAgentDataError(
            "PriceAgent", "TEL"
        )
        FailingPriceAgent.__name__ = "PriceAgent"

        def _ok_agent(name, analysis_cls, data_cls, analysis_text):
            mock = MagicMock()
            mock.return_value.run.return_value = analysis_cls(
                data=data_cls(symbol="TEL"),
                analysis=analysis_text,
            )
            mock.__name__ = name
            return mock

        # StockPrice requires current_price, so build it manually for the
        # other agents we don't care about here.
        OkDividendAgent = _ok_agent(
            "DividendAgent", DividendAnalysis, DividendInfo, "div ok"
        )
        OkMovementAgent = _ok_agent(
            "MovementAgent", MovementAnalysis, PriceMovement, "mov ok"
        )
        OkValuationAgent = _ok_agent(
            "ValuationAgent", ValuationAnalysis, FairValueEstimate, "val ok"
        )
        OkControversyAgent = _ok_agent(
            "ControversyAgent", ControversyAnalysis, ControversyInfo, "ctr ok"
        )
        OkSentimentAgent = _ok_agent(
            "SentimentAgent", SentimentAnalysis, SentimentInfo, "sen ok"
        )

        # Consolidator must NOT run when an error is present.
        MockConsolidator = MagicMock()

        mock_registry = [
            ("price_agent", "price_analysis", FailingPriceAgent),
            ("dividend_agent", "dividend_analysis", OkDividendAgent),
            ("movement_agent", "movement_analysis", OkMovementAgent),
            ("valuation_agent", "valuation_analysis", OkValuationAgent),
            ("controversy_agent", "controversy_analysis", OkControversyAgent),
            ("sentiment_agent", "sentiment_analysis", OkSentimentAgent),
        ]

        mock_llm = MagicMock()

        with (
            patch.object(workflow_mod, "AGENT_REGISTRY", mock_registry),
            patch.object(workflow_mod, "ConsolidatorAgent", MockConsolidator),
            patch.object(workflow_mod, "validate_symbol", return_value="TEL"),
        ):
            result = run_analysis("TEL", llm=mock_llm, mini_llm=mock_llm)

        # The pipeline must surface the empty-data error and skip the report.
        assert result.get("final_report") is None
        assert result.get("error") is not None
        assert "PriceAgent" in result["error"]
        assert "TEL" in result["error"]
        # The consolidator must never have been instantiated/run.
        MockConsolidator.return_value.run.assert_not_called()
