"""
Human-friendly classification of LLM provider errors.

The advisor talks to OpenAI / Anthropic through LangChain. When a call fails
for a reason the *user* cannot fix — most commonly an invalid or expired API
key — the raw provider exception (e.g. ``Error code: 401 - {'error': ...}``)
must never reach the UI. :func:`friendly_llm_error` maps such exceptions to a
clear, presentable sentence and returns ``None`` for everything else, so
callers keep their existing handling for unrelated failures.

Detection works by class name, HTTP status, and message text so it does not
need to import the OpenAI or Anthropic SDKs.
"""

from __future__ import annotations

from collections.abc import Iterator

AUTH_ERROR_MESSAGE = (
    "The AI service rejected our credentials — the API key is invalid or has "
    "expired, so analysis can't run right now. Please contact the site "
    "administrator to renew the key, then try again."
)

QUOTA_ERROR_MESSAGE = (
    "The AI service is temporarily unavailable — its usage quota or billing "
    "limit has been reached. Please contact the site administrator, then try again."
)


def _chain(exc: BaseException) -> Iterator[BaseException]:
    """Yield *exc* and its ``__cause__`` / ``__context__`` chain (once each)."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _status_of(exc: BaseException) -> int | None:
    for attr in ("status_code", "http_status", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    return None


def friendly_llm_error(exc: BaseException) -> str | None:
    """Return a user-facing message when *exc* is an LLM auth/quota failure.

    Recognises invalid/expired API keys (and, separately, an exhausted quota
    or billing limit) from OpenAI and Anthropic. Returns ``None`` for
    unrelated errors so the caller can fall back to its normal handling.
    """
    for err in _chain(exc):
        name = type(err).__name__
        status = _status_of(err)
        text = str(getattr(err, "message", "") or err).lower()

        # Exhausted quota / billing — a 429 carrying an ``insufficient_quota``
        # code, not an ordinary transient rate limit. Checked first so it is
        # not misread as a plain auth failure.
        if "insufficient_quota" in text or "billing" in text or "exceeded your current quota" in text:
            return QUOTA_ERROR_MESSAGE

        is_auth = (
            name in {"AuthenticationError", "PermissionDeniedError"}
            or status in {401, 403}
            or "invalid x-api-key" in text  # Anthropic
            or "incorrect api key" in text  # OpenAI
            or "invalid_api_key" in text
            or "unauthorized" in text
            or ("api key" in text and ("expired" in text or "invalid" in text))
        )
        if is_auth:
            return AUTH_ERROR_MESSAGE

    return None
