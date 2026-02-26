"""
Tests for the LangGraph workflow construction.

These tests verify graph structure and node wiring.
Integration tests that invoke the full graph with mocked agents are included.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import ph_stocks_advisor.graph.workflow as workflow_mod
from ph_stocks_advisor.graph.workflow import AGENT_REGISTRY, build_graph, run_analysis
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
    StockPrice,
    ValuationAnalysis,
    Verdict,
)


class TestBuildGraph:
    def test_graph_compiles(self):
        """The graph should compile without errors when given a mock LLM."""
        mock_llm = MagicMock()
        graph = build_graph(llm=mock_llm)
        assert graph is not None

    def test_graph_has_expected_nodes(self):
        """All specialist, validation, and consolidator nodes should be present."""
        mock_llm = MagicMock()
        graph = build_graph(llm=mock_llm)
        node_names = set(graph.get_graph().nodes.keys())
        expected = {
            "validate",
            "price_agent",
            "dividend_agent",
            "movement_agent",
            "valuation_agent",
            "controversy_agent",
            "consolidator",
        }
        assert expected.issubset(node_names)

    def test_registry_drives_node_creation(self):
        """Every agent in AGENT_REGISTRY should result in a graph node."""
        mock_llm = MagicMock()
        graph = build_graph(llm=mock_llm)
        node_names = set(graph.get_graph().nodes.keys())
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
        ]

        mock_llm = MagicMock()

        with (
            patch.object(workflow_mod, "AGENT_REGISTRY", mock_registry),
            patch.object(workflow_mod, "ConsolidatorAgent", MockConsolidator),
            patch.object(workflow_mod, "validate_symbol", return_value="TEL"),
        ):
            result = run_analysis("TEL", llm=mock_llm)

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
            result = run_analysis("XYZ", llm=mock_llm)

        assert result.get("error") is not None
        assert "XYZ" in result["error"]
        assert result.get("final_report") is None
