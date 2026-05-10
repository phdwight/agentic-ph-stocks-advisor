"""Long-term project memory.

A SQLite + sqlite-vec vector store that holds embeddings of source code,
rules, instructions and skills. Used by Copilot (and optionally agents)
as durable, retrievable context across sessions.

Public surface:

* :class:`VectorMemory`   – the store façade (open / search / upsert).
* :class:`Embedder`       – the embedding-provider Protocol.
* :class:`OpenAIEmbedder` – default OpenAI implementation.
* :func:`ingest_project`  – walk the workspace and (re-)index it.
"""

from ph_stocks_advisor.memory.embeddings import Embedder, OpenAIEmbedder
from ph_stocks_advisor.memory.ingest import ingest_project
from ph_stocks_advisor.memory.vector_store import SearchHit, VectorMemory

__all__ = [
    "Embedder",
    "OpenAIEmbedder",
    "SearchHit",
    "VectorMemory",
    "ingest_project",
]
