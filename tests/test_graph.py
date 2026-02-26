"""
Tests for the LangGraph workflow construction.

These tests verify graph structure and node wiring.
Integration tests that invoke the full graph with mocked agents are included.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from ph_stocks_advisor.graph.workflow import build_graph, run_analysis
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
        """The graph should compile without errors."""
        graph = build_graph()
        assert graph is not None

    def test_graph_has_expected_nodes(self):
        """All specialist, validation, and consolidator nodes should be present."""
        graph = build_graph()
        # LangGraph compiled graphs expose node names
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


class TestRunAnalysisIntegration:
    """Integration test that mocks all agents and runs the full graph."""

    @patch("ph_stocks_advisor.graph.workflow._validate_node", return_value={})
    @patch("ph_stocks_advisor.graph.workflow._consolidate_node")
    @patch("ph_stocks_advisor.graph.workflow._controversy_node")
    @patch("ph_stocks_advisor.graph.workflow._valuation_node")
    @patch("ph_stocks_advisor.graph.workflow._movement_node")
    @patch("ph_stocks_advisor.graph.workflow._dividend_node")
    @patch("ph_stocks_advisor.graph.workflow._price_node")
    def test_full_pipeline(
        self,
        mock_price,
        mock_dividend,
        mock_movement,
        mock_valuation,
        mock_controversy,
        mock_consolidate,
        mock_validate,
    ):
        # Set up mock return values
        mock_price.return_value = {
            "price_analysis": PriceAnalysis(
                data=StockPrice(symbol="TEL", current_price=1250.0),
                analysis="Price OK.",
            )
        }
        mock_dividend.return_value = {
            "dividend_analysis": DividendAnalysis(
                data=DividendInfo(symbol="TEL"),
                analysis="Dividend OK.",
            )
        }
        mock_movement.return_value = {
            "movement_analysis": MovementAnalysis(
                data=PriceMovement(symbol="TEL"),
                analysis="Movement OK.",
            )
        }
        mock_valuation.return_value = {
            "valuation_analysis": ValuationAnalysis(
                data=FairValueEstimate(symbol="TEL"),
                analysis="Valuation OK.",
            )
        }
        mock_controversy.return_value = {
            "controversy_analysis": ControversyAnalysis(
                data=ControversyInfo(symbol="TEL"),
                analysis="Risk OK.",
            )
        }
        mock_consolidate.return_value = {
            "final_report": FinalReport(
                symbol="TEL",
                verdict=Verdict.BUY,
                summary="TEL is a solid investment.",
            )
        }

        result = run_analysis("TEL")
        report = result["final_report"]

        # Handle both dict and FinalReport
        if isinstance(report, dict):
            report = FinalReport(**report)

        assert report.symbol == "TEL"
        assert report.verdict == Verdict.BUY
        assert "solid" in report.summary


class TestValidationFailure:
    """Test that an invalid symbol short-circuits the graph."""

    @patch(
        "ph_stocks_advisor.graph.workflow._validate_node",
        return_value={"error": "Symbol 'XYZ' not found on Yahoo Finance."},
    )
    def test_invalid_symbol_returns_error(self, mock_validate):
        result = run_analysis("XYZ")
        assert result.get("error") is not None
        assert "XYZ" in result["error"]
        assert result.get("final_report") is None
