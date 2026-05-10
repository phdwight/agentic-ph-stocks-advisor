"""Embedding providers for the long-term memory store.

Defines a narrow :class:`Embedder` protocol (Interface Segregation) so that
callers depend only on ``embed(texts) -> list[list[float]]``. Concrete
providers (OpenAI, fakes for tests) are drop-in substitutes
(Liskov Substitution).
"""

from __future__ import annotations

import os
from typing import Protocol, runtime_checkable


@runtime_checkable
class Embedder(Protocol):
    """A minimal embedding provider."""

    dim: int
    """Embedding dimensionality."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per input string."""
        ...


class OpenAIEmbedder:
    """Default :class:`Embedder` backed by the OpenAI embeddings API."""

    def __init__(
        self,
        model: str = "text-embedding-3-small",
        api_key: str | None = None,
        dim: int = 1536,
    ) -> None:
        # Imported lazily so test environments without the package can still
        # import this module and substitute a fake.
        from openai import OpenAI  # type: ignore[import-not-found]

        self._client = OpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY"))
        self._model = model
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = self._client.embeddings.create(model=self._model, input=texts)
        return [item.embedding for item in response.data]
