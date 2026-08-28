"""Tests for user-facing classification of LLM provider errors."""

from __future__ import annotations

from ph_stocks_advisor.infra.llm_errors import (
    AUTH_ERROR_MESSAGE,
    QUOTA_ERROR_MESSAGE,
    friendly_llm_error,
)


class _FakeAuthenticationError(Exception):
    """Mimics openai/anthropic ``AuthenticationError`` (class name + status)."""

    def __init__(self, message: str, status_code: int = 401) -> None:
        super().__init__(message)
        self.status_code = status_code


class _FakeRateLimitError(Exception):
    def __init__(self, message: str, status_code: int = 429) -> None:
        super().__init__(message)
        self.status_code = status_code


def test_openai_expired_key_is_recognised():
    exc = _FakeAuthenticationError("Error code: 401 - {'error': {'message': 'Incorrect API key provided'}}")
    assert friendly_llm_error(exc) == AUTH_ERROR_MESSAGE


def test_anthropic_invalid_key_is_recognised():
    exc = _FakeAuthenticationError("invalid x-api-key", status_code=401)
    assert friendly_llm_error(exc) == AUTH_ERROR_MESSAGE


def test_classification_by_status_code_alone():
    # Even a generically-named exception with a 401 status is treated as auth.
    exc = RuntimeError("something went wrong")
    exc.status_code = 403  # type: ignore[attr-defined]
    assert friendly_llm_error(exc) == AUTH_ERROR_MESSAGE


def test_expired_wording_in_message():
    exc = RuntimeError("Your API key has expired")
    assert friendly_llm_error(exc) == AUTH_ERROR_MESSAGE


def test_insufficient_quota_maps_to_quota_message():
    exc = _FakeRateLimitError("Error code: 429 - {'error': {'code': 'insufficient_quota'}}")
    assert friendly_llm_error(exc) == QUOTA_ERROR_MESSAGE


def test_wrapped_cause_is_inspected():
    inner = _FakeAuthenticationError("invalid_api_key")
    outer = RuntimeError("agent failed")
    outer.__cause__ = inner
    assert friendly_llm_error(outer) == AUTH_ERROR_MESSAGE


def test_unrelated_error_returns_none():
    assert friendly_llm_error(RuntimeError("Timed out waiting for MCP session")) is None
    assert friendly_llm_error(ValueError("bad symbol")) is None


def test_plain_rate_limit_is_not_misclassified():
    # An ordinary 429 (no quota/billing wording) is not our concern here.
    exc = _FakeRateLimitError("Rate limit reached for requests")
    assert friendly_llm_error(exc) is None
