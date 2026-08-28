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


class TestGracefulDegradation:
    """A failing specialist must NOT stop the run: the pipeline continues,
    the dimension is recorded in ``data_gaps`` (excluded from the score),
    and the report can state the absence. Only an all-agents failure or an
    invalid symbol aborts."""

    @staticmethod
    def _ok_agent(name, analysis_cls, data_cls, analysis_text, symbol="TEL"):
        mock = MagicMock()
        mock.return_value.run.return_value = analysis_cls(
            data=data_cls(symbol=symbol),
            analysis=analysis_text,
        )
        mock.__name__ = name
        return mock

    def _registry_with_failing(self, failing_names, side_effect, symbol="TEL"):
        entries = [
            ("price_agent", "price_analysis", PriceAnalysis, None, "PriceAgent"),
            ("dividend_agent", "dividend_analysis", DividendAnalysis, DividendInfo, "DividendAgent"),
            ("movement_agent", "movement_analysis", MovementAnalysis, PriceMovement, "MovementAgent"),
            ("valuation_agent", "valuation_analysis", ValuationAnalysis, FairValueEstimate, "ValuationAgent"),
            ("controversy_agent", "controversy_analysis", ControversyAnalysis, ControversyInfo, "ControversyAgent"),
            ("sentiment_agent", "sentiment_analysis", SentimentAnalysis, SentimentInfo, "SentimentAgent"),
        ]
        registry = []
        for node, key, analysis_cls, data_cls, name in entries:
            if name in failing_names:
                failing = MagicMock()
                failing.return_value.run.side_effect = side_effect
                failing.__name__ = name
                registry.append((node, key, failing))
            elif name == "PriceAgent":
                ok = MagicMock()
                ok.return_value.run.return_value = PriceAnalysis(
                    data=StockPrice(symbol=symbol, current_price=100.0), analysis="price ok"
                )
                ok.__name__ = name
                registry.append((node, key, ok))
            else:
                registry.append((node, key, self._ok_agent(name, analysis_cls, data_cls, f"{name} ok", symbol)))
        return registry

    @staticmethod
    def _consolidator_returning(symbol="TEL"):
        mock = MagicMock()
        mock.return_value.run.return_value = FinalReport(
            symbol=symbol,
            verdict=Verdict.BUY,
            summary="**Executive Summary:**\nok",
            score=70,
        )
        return mock

    def test_empty_dividend_data_continues_and_reports_gap(self):
        """A ticker without dividends completes with the gap recorded."""
        from ph_stocks_advisor.agents.specialists import EmptyAgentDataError

        registry = self._registry_with_failing({"DividendAgent"}, EmptyAgentDataError("DividendAgent", "TEL"))
        MockConsolidator = self._consolidator_returning()
        mock_llm = MagicMock()

        with (
            patch.object(workflow_mod, "AGENT_REGISTRY", registry),
            patch.object(workflow_mod, "ConsolidatorAgent", MockConsolidator),
            patch.object(workflow_mod, "validate_symbol", return_value="TEL"),
        ):
            result = run_analysis("TEL", llm=mock_llm, mini_llm=mock_llm)

        assert result.get("error") is None
        assert result.get("final_report") is not None
        advisor_state = MockConsolidator.return_value.run.call_args[0][0]
        assert advisor_state.data_gaps == ["dividend_analysis"]
        assert "DATA UNAVAILABLE" in advisor_state.dividend_analysis.analysis
        assert "dividend" in advisor_state.dividend_analysis.analysis
        # The healthy dimensions flowed through untouched.
        assert advisor_state.price_analysis.analysis == "price ok"

    def test_transport_error_continues_with_gap(self):
        """A transient failure (MCP timeout) degrades instead of aborting."""
        registry = self._registry_with_failing({"MovementAgent"}, RuntimeError("Timed out waiting for MCP session"))
        MockConsolidator = self._consolidator_returning()
        mock_llm = MagicMock()

        with (
            patch.object(workflow_mod, "AGENT_REGISTRY", registry),
            patch.object(workflow_mod, "ConsolidatorAgent", MockConsolidator),
            patch.object(workflow_mod, "validate_symbol", return_value="TEL"),
        ):
            result = run_analysis("TEL", llm=mock_llm, mini_llm=mock_llm)

        assert result.get("error") is None
        assert result.get("final_report") is not None
        advisor_state = MockConsolidator.return_value.run.call_args[0][0]
        assert advisor_state.data_gaps == ["movement_analysis"]
        assert "temporary error" in advisor_state.movement_analysis.analysis

    def test_all_agents_failing_aborts(self):
        """Systemic failure (every dimension gone) must still abort — there
        is nothing real to consolidate."""
        registry = self._registry_with_failing(
            {"PriceAgent", "DividendAgent", "MovementAgent", "ValuationAgent", "ControversyAgent", "SentimentAgent"},
            RuntimeError("everything is down"),
        )
        MockConsolidator = MagicMock()
        mock_llm = MagicMock()

        with (
            patch.object(workflow_mod, "AGENT_REGISTRY", registry),
            patch.object(workflow_mod, "ConsolidatorAgent", MockConsolidator),
            patch.object(workflow_mod, "validate_symbol", return_value="TEL"),
        ):
            result = run_analysis("TEL", llm=mock_llm, mini_llm=mock_llm)

        assert result.get("final_report") is None
        assert result.get("error") is not None
        assert "No specialist agent could produce data" in result["error"]
        MockConsolidator.return_value.run.assert_not_called()

    def test_invalid_api_key_surfaces_actionable_error(self):
        """An expired/invalid LLM key fails every agent — the run must report
        the real, actionable reason, not the generic 'no data' abort."""
        from ph_stocks_advisor.infra.llm_errors import AUTH_ERROR_MESSAGE

        class _AuthError(Exception):
            def __init__(self, message: str) -> None:
                super().__init__(message)
                self.status_code = 401

        registry = self._registry_with_failing(
            {"PriceAgent", "DividendAgent", "MovementAgent", "ValuationAgent", "ControversyAgent", "SentimentAgent"},
            _AuthError("Error code: 401 - Incorrect API key provided"),
        )
        MockConsolidator = MagicMock()
        mock_llm = MagicMock()

        with (
            patch.object(workflow_mod, "AGENT_REGISTRY", registry),
            patch.object(workflow_mod, "ConsolidatorAgent", MockConsolidator),
            patch.object(workflow_mod, "validate_symbol", return_value="TEL"),
        ):
            result = run_analysis("TEL", llm=mock_llm, mini_llm=mock_llm)

        assert result.get("final_report") is None
        assert result.get("error") == AUTH_ERROR_MESSAGE
        assert "No specialist agent could produce data" not in result["error"]
        MockConsolidator.return_value.run.assert_not_called()
