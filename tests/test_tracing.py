"""Tests for Langfuse tracing integration."""

from __future__ import annotations

import sys
import types
from typing import Any, cast
from unittest.mock import MagicMock, patch

from ph_stocks_advisor.infra import tracing


class TestBuildLangfuseConfig:
    def test_returns_empty_when_keys_missing(self, monkeypatch):
        monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
        monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
        assert tracing.build_langfuse_config(run_name="t") == {}

    def test_returns_empty_when_explicitly_disabled(self, monkeypatch):
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
        monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "false")
        assert tracing.build_langfuse_config(run_name="t") == {}

    def _patch_langfuse(self, sentinel):
        fake_module = types.ModuleType("langfuse.langchain")
        cast(Any, fake_module).CallbackHandler = MagicMock(return_value=sentinel)
        fake_pkg = types.ModuleType("langfuse")
        cast(Any, fake_pkg).langchain = fake_module
        return patch.dict(
            sys.modules,
            {"langfuse": fake_pkg, "langfuse.langchain": fake_module},
        )

    def test_includes_callback_run_name_and_attributes(self, monkeypatch):
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
        monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "true")

        sentinel = object()
        with self._patch_langfuse(sentinel):
            cfg = tracing.build_langfuse_config(
                run_name="stock-analysis",
                user_id="user-123",
                session_id="task-abc",
                tags=["stock-analysis", "ph-stocks-advisor"],
                metadata={"symbol": "TEL"},
            )

        assert cfg["callbacks"] == [sentinel]
        assert cfg["run_name"] == "stock-analysis"
        md = cfg["metadata"]
        assert md["langfuse_user_id"] == "user-123"
        assert md["langfuse_session_id"] == "task-abc"
        assert md["langfuse_tags"] == ["stock-analysis", "ph-stocks-advisor"]
        assert md["symbol"] == "TEL"

    def test_omits_optional_attributes_when_not_provided(self, monkeypatch):
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
        monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "true")

        sentinel = object()
        with self._patch_langfuse(sentinel):
            cfg = tracing.build_langfuse_config(run_name="bare")

        assert cfg == {"callbacks": [sentinel], "run_name": "bare"}


class TestFlushLangfuse:
    def test_noop_when_disabled(self, monkeypatch):
        monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
        monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
        tracing.flush_langfuse()  # must not raise

    def test_calls_get_client_flush_when_enabled(self, monkeypatch):
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
        monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "true")

        client = MagicMock()
        fake_pkg = types.ModuleType("langfuse")
        cast(Any, fake_pkg).get_client = MagicMock(return_value=client)
        with patch.dict(sys.modules, {"langfuse": fake_pkg}):
            tracing.flush_langfuse()

        client.flush.assert_called_once()
