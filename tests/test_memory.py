"""Behavioural tests for the long-term project memory store."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from ph_stocks_advisor.memory.ingest import ingest_project
from ph_stocks_advisor.memory.vector_store import VectorMemory


class HashEmbedder:
    """Deterministic embedder that maps tokens to a tiny vector space.

    Each unique whitespace-token gets a slot in a fixed-size vector; the
    vector value at that slot is the token's frequency. This is enough to
    exercise sqlite-vec similarity search in tests without calling OpenAI.
    """

    dim = 64

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            vec = [0.0] * self.dim
            for token in text.lower().split():
                slot = int(hashlib.sha1(token.encode()).hexdigest(), 16) % self.dim
                vec[slot] += 1.0
            # L2-normalise to keep distances bounded.
            norm = sum(v * v for v in vec) ** 0.5 or 1.0
            out.append([v / norm for v in vec])
        return out


@pytest.fixture
def store(tmp_path: Path) -> VectorMemory:
    s = VectorMemory(tmp_path / "mem.db", HashEmbedder())
    yield s
    s.close()


def test_upsert_then_search_returns_relevant_chunk(store: VectorMemory) -> None:
    store.upsert_document(
        source_path="docs/dividends.md",
        source_type="doc",
        title="dividends.md",
        content="Dividend agent flags REIT payout sustainability under RA 9856.",
        chunks=["Dividend agent flags REIT payout sustainability under RA 9856."],
    )
    store.upsert_document(
        source_path="docs/price.md",
        source_type="doc",
        title="price.md",
        content="Price agent compares current price to the 52-week trading range.",
        chunks=["Price agent compares current price to the 52-week trading range."],
    )

    hits = store.search("REIT dividend payout rules", k=2)

    assert hits, "expected at least one hit"
    assert hits[0].source_path == "docs/dividends.md"


def test_unchanged_content_is_not_reembedded(store: VectorMemory) -> None:
    text = "stable content that does not change"
    chunks = [text]
    first = store.upsert_document(
        source_path="a.md",
        source_type="doc",
        title="a.md",
        content=text,
        chunks=chunks,
    )
    second = store.upsert_document(
        source_path="a.md",
        source_type="doc",
        title="a.md",
        content=text,
        chunks=chunks,
    )

    assert first is True
    assert second is False
    assert store.stats() == {"documents": 1, "chunks": 1}


def test_updating_content_replaces_chunks(store: VectorMemory) -> None:
    store.upsert_document(
        source_path="a.md",
        source_type="doc",
        title="a.md",
        content="original",
        chunks=["original line one", "original line two"],
    )
    store.upsert_document(
        source_path="a.md",
        source_type="doc",
        title="a.md",
        content="rewritten",
        chunks=["rewritten line"],
    )

    assert store.stats() == {"documents": 1, "chunks": 1}
    hits = store.search("rewritten", k=1)
    assert hits and hits[0].text == "rewritten line"


def test_forget_removes_document_and_vectors(store: VectorMemory) -> None:
    store.upsert_document(
        source_path="gone.md",
        source_type="doc",
        title="gone.md",
        content="to be deleted",
        chunks=["to be deleted"],
    )
    assert store.forget("gone.md") is True
    assert store.stats() == {"documents": 0, "chunks": 0}
    assert store.search("deleted", k=3) == []


def test_search_can_filter_by_source_type(store: VectorMemory) -> None:
    store.upsert_document(
        source_path="rules.md",
        source_type="rule",
        title="rules.md",
        content="follow SOLID principles strictly",
        chunks=["follow SOLID principles strictly"],
    )
    store.upsert_document(
        source_path="code.py",
        source_type="code",
        title="code.py",
        content="def solid(): return True",
        chunks=["def solid(): return True"],
    )

    hits = store.search("SOLID", k=5, source_type="rule")
    assert all(h.source_type == "rule" for h in hits)
    assert hits and hits[0].source_path == "rules.md"


def test_ingest_project_classifies_rules_skills_and_code(tmp_path: Path) -> None:
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "copilot-instructions.md").write_text(
        "# Project rules\nAlways update the README."
    )
    (tmp_path / "skills").mkdir()
    (tmp_path / "skills" / "SKILL.md").write_text("# Skill\nDo the thing.")
    (tmp_path / "module.py").write_text("def hello():\n    return 'world'\n")
    (tmp_path / "README.md").write_text("# Project\nOverview text.")
    # Ignored.
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "junk.py").write_text("ignored = True")

    store = VectorMemory(tmp_path / "mem.db", HashEmbedder())
    try:
        result = ingest_project(tmp_path, store)
        assert result.indexed == 4

        types = {h.source_type for h in store.search("rules skill code overview", k=10)}
        assert {"rule", "skill", "code", "doc"}.issubset(types)
    finally:
        store.close()


def test_ingest_prunes_files_that_disappear(tmp_path: Path) -> None:
    target = tmp_path / "doomed.md"
    target.write_text("temporary note")

    store = VectorMemory(tmp_path / "mem.db", HashEmbedder())
    try:
        ingest_project(tmp_path, store)
        assert "doomed.md" in store.known_paths()

        target.unlink()
        result = ingest_project(tmp_path, store)
        assert result.removed == 1
        assert "doomed.md" not in store.known_paths()
    finally:
        store.close()


def test_ingest_is_idempotent_for_unchanged_files(tmp_path: Path) -> None:
    (tmp_path / "stable.md").write_text("content that never changes")

    store = VectorMemory(tmp_path / "mem.db", HashEmbedder())
    try:
        first = ingest_project(tmp_path, store)
        second = ingest_project(tmp_path, store)
        assert first.indexed == 1
        assert second.indexed == 0
        assert second.unchanged == 1
    finally:
        store.close()
