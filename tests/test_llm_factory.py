"""Tests for the provider-agnostic LLM factory.

Covers spec parsing, provider→class mapping, per-agent assignment, fail-fast
on a missing key, and that temperature is sent to OpenAI but never to
Anthropic (current Claude models reject a caller-set temperature). No network
calls — the LLM classes are constructed but not invoked.
"""

from __future__ import annotations

import pytest

from ph_stocks_advisor.infra import config as cfg


def _model_id(model) -> str:
    """Provider-agnostic model id (OpenAI uses ``model_name``, Anthropic ``model``)."""
    return str(getattr(model, "model_name", None) or getattr(model, "model", None) or "")


@pytest.fixture
def settings():
    """A Settings instance with both provider keys populated."""
    s = cfg.Settings()
    s.openai_api_key = "sk-openai-test"
    s.anthropic_api_key = "sk-ant-test"
    s.llm_provider = "openai"
    s.openai_model_large = "gpt-4o"
    s.openai_model_medium = "gpt-4o-mini"
    s.openai_model_small = "gpt-4o-mini"
    s.anthropic_model_large = "claude-opus-4-8"
    s.anthropic_model_medium = "claude-sonnet-5"
    s.anthropic_model_small = "claude-haiku-4-5"
    return s


# ---------------------------------------------------------------------------
# Spec parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "spec,provider,tier",
    [
        ("openai:large", "openai", "large"),
        ("anthropic:small", "anthropic", "small"),
        ("ANTHROPIC:Medium", "anthropic", "medium"),  # case-insensitive
        ("large", "openai", "large"),  # bare tier → default provider
    ],
)
def test_resolve_spec(spec, provider, tier, settings):
    assert cfg._resolve_spec(spec, settings) == (provider, tier)


def test_bare_tier_follows_default_provider(settings):
    settings.llm_provider = "anthropic"
    assert cfg._resolve_spec("small", settings) == ("anthropic", "small")


@pytest.mark.parametrize("bad", ["openai:huge", "gemini:large", "medium:openai", ""])
def test_resolve_spec_rejects_bad_input(bad, settings):
    with pytest.raises(ValueError):
        cfg._resolve_spec(bad, settings)


# ---------------------------------------------------------------------------
# Provider → class mapping + model selection
# ---------------------------------------------------------------------------


def test_openai_spec_builds_chatopenai_with_temperature(settings):
    from langchain_openai import ChatOpenAI

    model = cfg.build_chat_model("openai:large", settings)
    assert isinstance(model, ChatOpenAI)
    assert _model_id(model) == "gpt-4o"
    assert model.temperature == settings.llm_temperature


def test_anthropic_spec_builds_chatanthropic_without_temperature(settings):
    from langchain_anthropic import ChatAnthropic

    model = cfg.build_chat_model("anthropic:medium", settings)
    assert isinstance(model, ChatAnthropic)
    assert _model_id(model) == "claude-sonnet-5"
    assert model.max_tokens == settings.llm_max_tokens
    # Current Claude models reject a caller-set temperature — the factory must
    # not send one. langchain-anthropic leaves it unset (None) when omitted.
    assert model.temperature is None


def test_each_tier_selects_the_right_model(settings):
    assert _model_id(cfg.build_chat_model("openai:small", settings)) == "gpt-4o-mini"
    assert _model_id(cfg.build_chat_model("anthropic:large", settings)) == "claude-opus-4-8"
    assert _model_id(cfg.build_chat_model("anthropic:small", settings)) == "claude-haiku-4-5"


# ---------------------------------------------------------------------------
# Fail-fast on missing key
# ---------------------------------------------------------------------------


def test_missing_openai_key_fails_fast(settings):
    settings.openai_api_key = ""
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        cfg.build_chat_model("openai:large", settings)


def test_missing_anthropic_key_fails_fast(settings):
    settings.anthropic_api_key = ""
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        cfg.build_chat_model("anthropic:large", settings)


# ---------------------------------------------------------------------------
# Per-agent assignment
# ---------------------------------------------------------------------------


def test_agent_specs_cover_every_workflow_agent():
    """Every specialist node name + consolidator + portfolio has a spec."""
    from ph_stocks_advisor.graph.workflow import AGENT_REGISTRY

    for node_name, _state_key, _cls in AGENT_REGISTRY:
        assert node_name in cfg.AGENT_LLM_SPECS, node_name
    assert "consolidator" in cfg.AGENT_LLM_SPECS
    assert "portfolio" in cfg.AGENT_LLM_SPECS


def test_get_agent_llm_uses_the_agents_spec(settings, monkeypatch):
    # Consolidator defaults to the large tier; a specialist to small.
    monkeypatch.setitem(cfg.AGENT_LLM_SPECS, "consolidator", "openai:large")
    monkeypatch.setitem(cfg.AGENT_LLM_SPECS, "price_agent", "openai:small")
    assert _model_id(cfg.get_agent_llm("consolidator", settings)) == "gpt-4o"
    assert _model_id(cfg.get_agent_llm("price_agent", settings)) == "gpt-4o-mini"


def test_agents_can_mix_providers(settings, monkeypatch):
    """The whole point: one run, different providers per agent."""
    monkeypatch.setitem(cfg.AGENT_LLM_SPECS, "consolidator", "anthropic:large")
    monkeypatch.setitem(cfg.AGENT_LLM_SPECS, "price_agent", "openai:small")
    from langchain_anthropic import ChatAnthropic
    from langchain_openai import ChatOpenAI

    assert isinstance(cfg.get_agent_llm("consolidator", settings), ChatAnthropic)
    assert isinstance(cfg.get_agent_llm("price_agent", settings), ChatOpenAI)


# ---------------------------------------------------------------------------
# Back-compat shims
# ---------------------------------------------------------------------------


def test_get_llm_and_mini_llm_still_work(settings, monkeypatch):
    monkeypatch.setitem(cfg.AGENT_LLM_SPECS, "consolidator", "openai:large")
    assert _model_id(cfg.get_llm(settings)) == "gpt-4o"  # consolidator spec
    assert _model_id(cfg.get_mini_llm(settings)) == "gpt-4o-mini"  # active provider small
