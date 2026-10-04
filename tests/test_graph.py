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


def _mock_specialist(
    name,
    analysis_cls,
    data_instance,
    *,
    analysis_text="ok",
    fetch_error=None,
    analyze_error=None,
):
    """Build a mock specialist whose ``fetch``/``analyze``/``wrap`` mirror the
    real :class:`SpecialistAgent` split, so tests exercise the node's
    fetch-then-narrate flow (and data-preservation on narration failure)."""
    agent = MagicMock()
    inst = agent.return_value
    if fetch_error is not None:
        inst.fetch.side_effect = fetch_error
    else:
        inst.fetch.return_value = data_instance
    if analyze_error is not None:
        inst.analyze.side_effect = analyze_error
    else:
        inst.analyze.return_value = analysis_cls(data=data_instance, analysis=analysis_text)
    # Real wrap() keeps the fetched data and swaps in the given note.
    inst.wrap.side_effect = lambda data, note: analysis_cls(data=data, analysis=note)
    agent.__name__ = name
    return agent


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
        MockPriceAgent = _mock_specialist(
            "PriceAgent", PriceAnalysis, StockPrice(symbol="TEL", current_price=1250.0), analysis_text="Price OK."
        )
        MockDividendAgent = _mock_specialist(
            "DividendAgent", DividendAnalysis, DividendInfo(symbol="TEL"), analysis_text="Dividend OK."
        )
        MockMovementAgent = _mock_specialist(
            "MovementAgent", MovementAnalysis, PriceMovement(symbol="TEL"), analysis_text="Movement OK."
        )
        MockValuationAgent = _mock_specialist(
            "ValuationAgent", ValuationAnalysis, FairValueEstimate(symbol="TEL"), analysis_text="Valuation OK."
        )
        MockControversyAgent = _mock_specialist(
            "ControversyAgent", ControversyAnalysis, ControversyInfo(symbol="TEL"), analysis_text="Risk OK."
        )
        MockSentimentAgent = _mock_specialist(
            "SentimentAgent", SentimentAnalysis, SentimentInfo(symbol="TEL"), analysis_text="Sentiment OK."
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

    def _registry_with_failing(self, failing_names, side_effect, symbol="TEL"):
        entries = [
            ("price_agent", "price_analysis", PriceAnalysis, StockPrice, "PriceAgent"),
            ("dividend_agent", "dividend_analysis", DividendAnalysis, DividendInfo, "DividendAgent"),
            ("movement_agent", "movement_analysis", MovementAnalysis, PriceMovement, "MovementAgent"),
            ("valuation_agent", "valuation_analysis", ValuationAnalysis, FairValueEstimate, "ValuationAgent"),
            ("controversy_agent", "controversy_analysis", ControversyAnalysis, ControversyInfo, "ControversyAgent"),
            ("sentiment_agent", "sentiment_analysis", SentimentAnalysis, SentimentInfo, "SentimentAgent"),
        ]
        registry = []
        for node, key, analysis_cls, data_cls, name in entries:
            data_instance = (
                StockPrice(symbol=symbol, current_price=100.0) if name == "PriceAgent" else data_cls(symbol=symbol)
            )
            if name in failing_names:
                # The scenarios here are all data-fetch failures (empty data,
                # MCP timeout, auth) — model them on ``fetch``.
                agent = _mock_specialist(name, analysis_cls, data_instance, fetch_error=side_effect)
            else:
                text = "price ok" if name == "PriceAgent" else f"{name} ok"
                agent = _mock_specialist(name, analysis_cls, data_instance, analysis_text=text)
            registry.append((node, key, agent))
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

    def test_narration_failure_preserves_fetched_data(self):
        """When the LLM narrative fails but data WAS fetched, the data (e.g. the
        movement trend-line series) is kept so visualisations still render; the
        dimension is still excluded from the verdict score."""
        symbol = "TEL"
        trend = [100.0, 101.0, 102.0, 103.0, 104.0]
        registry = []
        entries = [
            ("price_agent", "price_analysis", PriceAnalysis, StockPrice(symbol=symbol, current_price=100.0)),
            ("dividend_agent", "dividend_analysis", DividendAnalysis, DividendInfo(symbol=symbol)),
            ("valuation_agent", "valuation_analysis", ValuationAnalysis, FairValueEstimate(symbol=symbol)),
            ("controversy_agent", "controversy_analysis", ControversyAnalysis, ControversyInfo(symbol=symbol)),
            ("sentiment_agent", "sentiment_analysis", SentimentAnalysis, SentimentInfo(symbol=symbol)),
        ]
        for node, key, cls, data in entries:
            registry.append((node, key, _mock_specialist(node, cls, data, analysis_text=f"{node} ok")))
        # Movement: fetch returns a populated series, but narration (analyze) fails.
        movement_data = PriceMovement(symbol=symbol, year_start_price=100.0, year_end_price=104.0, monthly_prices=trend)
        movement_agent = _mock_specialist(
            "MovementAgent",
            MovementAnalysis,
            movement_data,
            analyze_error=RuntimeError("LLM narration timed out"),
        )
        registry.insert(2, ("movement_agent", "movement_analysis", movement_agent))

        MockConsolidator = MagicMock()
        MockConsolidator.return_value.run.return_value = FinalReport(
            symbol=symbol, verdict=Verdict.NOT_BUY, summary="ok", score=60
        )
        mock_llm = MagicMock()

        with (
            patch.object(workflow_mod, "AGENT_REGISTRY", registry),
            patch.object(workflow_mod, "ConsolidatorAgent", MockConsolidator),
            patch.object(workflow_mod, "validate_symbol", return_value=symbol),
        ):
            result = run_analysis(symbol, llm=mock_llm, mini_llm=mock_llm)

        assert result.get("error") is None
        assert result.get("final_report") is not None
        advisor_state = MockConsolidator.return_value.run.call_args[0][0]
        # Excluded from the score, but the fetched trend-line data survives.
        assert advisor_state.data_gaps == ["movement_analysis"]
        assert advisor_state.movement_analysis.data.monthly_prices == trend
        assert "NARRATIVE UNAVAILABLE" in advisor_state.movement_analysis.analysis

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
