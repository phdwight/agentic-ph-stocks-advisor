"""SQLite + sqlite-vec vector store for long-term project memory.

Single Responsibility: storage and similarity search only. Embedding is
delegated to an injected :class:`~ph_stocks_advisor.memory.embeddings.Embedder`
(Dependency Inversion).
"""

from __future__ import annotations

import hashlib
import sqlite3
import struct
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import sqlite_vec  # type: ignore[import-not-found]

from ph_stocks_advisor.memory.embeddings import Embedder

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source_path  TEXT    NOT NULL UNIQUE,
    source_type  TEXT    NOT NULL,
    title        TEXT    NOT NULL DEFAULT '',
    content_hash TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id    INTEGER NOT NULL,
    chunk_idx INTEGER NOT NULL,
    text      TEXT    NOT NULL,
    FOREIGN KEY (doc_id) REFERENCES documents(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
"""


@dataclass(frozen=True)
class SearchHit:
    """One semantic-search result."""

    source_path: str
    source_type: str
    title: str
    text: str
    distance: float


def _serialize_vector(vec: list[float]) -> bytes:
    """Pack a float32 vector for sqlite-vec BLOB columns."""
    return struct.pack(f"{len(vec)}f", *vec)


def hash_content(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class VectorMemory:
    """Persistent vector store backed by SQLite + sqlite-vec."""

    def __init__(self, db_path: str | Path, embedder: Embedder) -> None:
        self.db_path = Path(db_path)
        self.embedder = embedder
        self._conn = self._open(self.db_path)
        self._ensure_schema()

    # -- lifecycle ---------------------------------------------------------

    @staticmethod
    def _open(path: Path) -> sqlite3.Connection:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path))
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _ensure_schema(self) -> None:
        with self._conn:
            self._conn.executescript(_SCHEMA)
            self._conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks "
                f"USING vec0(embedding float[{self.embedder.dim}])"
            )

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> VectorMemory:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # -- write -------------------------------------------------------------

    def upsert_document(
        self,
        *,
        source_path: str,
        source_type: str,
        title: str,
        content: str,
        chunks: list[str],
    ) -> bool:
        """Insert or refresh a document plus its chunk embeddings.

        Returns ``True`` when the document was (re-)embedded, ``False`` when
        the stored content hash already matched and nothing changed.
        """
        digest = hash_content(content)
        cur = self._conn.execute(
            "SELECT id, content_hash FROM documents WHERE source_path = ?",
            (source_path,),
        )
        row = cur.fetchone()
        now = datetime.now(UTC).isoformat()

        if row is not None and row[1] == digest:
            return False

        embeddings = self.embedder.embed(chunks) if chunks else []

        with self._conn:
            if row is None:
                cur = self._conn.execute(
                    "INSERT INTO documents (source_path, source_type, title, content_hash, updated_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (source_path, source_type, title, digest, now),
                )
                doc_id = cur.lastrowid
            else:
                doc_id = row[0]
                self._conn.execute(
                    "UPDATE documents SET source_type=?, title=?, content_hash=?, updated_at=? WHERE id=?",
                    (source_type, title, digest, now, doc_id),
                )
                # Drop old chunks + their vectors.
                old_ids = [
                    r[0]
                    for r in self._conn.execute(
                        "SELECT id FROM chunks WHERE doc_id = ?", (doc_id,)
                    )
                ]
                if old_ids:
                    placeholders = ",".join("?" * len(old_ids))
                    self._conn.execute(
                        f"DELETE FROM vec_chunks WHERE rowid IN ({placeholders})",
                        old_ids,
                    )
                    self._conn.execute(
                        f"DELETE FROM chunks WHERE id IN ({placeholders})", old_ids
                    )

            for idx, (text, vec) in enumerate(zip(chunks, embeddings, strict=True)):
                cur = self._conn.execute(
                    "INSERT INTO chunks (doc_id, chunk_idx, text) VALUES (?, ?, ?)",
                    (doc_id, idx, text),
                )
                chunk_id = cur.lastrowid
                self._conn.execute(
                    "INSERT INTO vec_chunks(rowid, embedding) VALUES (?, ?)",
                    (chunk_id, _serialize_vector(vec)),
                )
        return True

    def forget(self, source_path: str) -> bool:
        """Remove a document and all of its chunks. Returns True if removed."""
        cur = self._conn.execute(
            "SELECT id FROM documents WHERE source_path = ?", (source_path,)
        )
        row = cur.fetchone()
        if row is None:
            return False
        doc_id = row[0]
        with self._conn:
            chunk_ids = [
                r[0]
                for r in self._conn.execute(
                    "SELECT id FROM chunks WHERE doc_id = ?", (doc_id,)
                )
            ]
            if chunk_ids:
                placeholders = ",".join("?" * len(chunk_ids))
                self._conn.execute(
                    f"DELETE FROM vec_chunks WHERE rowid IN ({placeholders})", chunk_ids
                )
            self._conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        return True

    # -- read --------------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        k: int = 5,
        source_type: str | None = None,
    ) -> list[SearchHit]:
        if not query.strip():
            return []
        [vec] = self.embedder.embed([query])
        params: list[object] = [_serialize_vector(vec), k * 4 if source_type else k]
        sql = (
            "SELECT c.text, d.source_path, d.source_type, d.title, v.distance "
            "FROM vec_chunks v "
            "JOIN chunks c ON c.id = v.rowid "
            "JOIN documents d ON d.id = c.doc_id "
            "WHERE v.embedding MATCH ? AND k = ? "
            "ORDER BY v.distance"
        )
        rows = self._conn.execute(sql, params).fetchall()
        hits = [
            SearchHit(
                source_path=path,
                source_type=stype,
                title=title,
                text=text,
                distance=float(dist),
            )
            for text, path, stype, title, dist in rows
            if source_type is None or stype == source_type
        ]
        return hits[:k]

    def known_paths(self) -> dict[str, str]:
        """Return ``{source_path: content_hash}`` for every stored document."""
        return {
            path: digest
            for path, digest in self._conn.execute(
                "SELECT source_path, content_hash FROM documents"
            )
        }

    def stats(self) -> dict[str, int]:
        docs = self._conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        chunks = self._conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        return {"documents": int(docs), "chunks": int(chunks)}
