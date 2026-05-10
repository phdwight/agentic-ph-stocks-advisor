"""Walk the workspace and feed it into :class:`VectorMemory`.

Single Responsibility: discover files, classify them, chunk them, and hand
them to the store. The store decides whether re-embedding is needed.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from ph_stocks_advisor.memory.vector_store import VectorMemory

# File-type classification by relative path patterns.
_RULES_PATTERNS: tuple[str, ...] = (
    ".github/copilot-instructions.md",
    ".github/instructions/*",
    "AGENTS.md",
    "**/AGENTS.md",
)

_SKILL_PATTERNS: tuple[str, ...] = (
    "**/SKILL.md",
    "**/*.skill.md",
)

_CODE_SUFFIXES: frozenset[str] = frozenset(
    {".py", ".pyi", ".toml", ".yaml", ".yml", ".json", ".cfg", ".ini", ".sh"}
)
_DOC_SUFFIXES: frozenset[str] = frozenset({".md", ".rst", ".txt"})

_DEFAULT_EXCLUDES: tuple[str, ...] = (
    ".git/*",
    ".venv/*",
    "venv/*",
    "node_modules/*",
    "__pycache__/*",
    "**/__pycache__/*",
    "*.egg-info/*",
    "**/*.egg-info/*",
    "db/*",
    "flask_session/*",
    "output/*",
    ".copilot-memory.db",
    ".copilot-memory.db-*",
    "reports.db",
    "reports.db-*",
    "dist/*",
    "build/*",
    ".pytest_cache/*",
    ".ruff_cache/*",
)

_MAX_FILE_BYTES = 200_000  # Skip very large files; not useful in a vector index.
_CHUNK_CHARS = 1_400
_CHUNK_OVERLAP = 200


@dataclass(frozen=True)
class IngestResult:
    scanned: int
    indexed: int
    unchanged: int
    removed: int
    skipped: int


def _match_any(rel: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatch(rel, p) for p in patterns)


def _classify(rel_path: str, suffix: str) -> str | None:
    if _match_any(rel_path, _RULES_PATTERNS):
        return "rule"
    if _match_any(rel_path, _SKILL_PATTERNS):
        return "skill"
    if suffix in _CODE_SUFFIXES:
        return "code"
    if suffix in _DOC_SUFFIXES:
        return "doc"
    return None


def _iter_candidate_files(root: Path, excludes: tuple[str, ...]) -> Iterator[Path]:
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if _match_any(rel, excludes):
            continue
        yield path


def _chunk_text(text: str, *, size: int = _CHUNK_CHARS, overlap: int = _CHUNK_OVERLAP) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        chunks.append(text[start:end])
        if end == len(text):
            break
        start = end - overlap
    return chunks


def ingest_project(
    root: str | Path,
    store: VectorMemory,
    *,
    extra_excludes: tuple[str, ...] = (),
    prune_missing: bool = True,
) -> IngestResult:
    """Index every supported file under *root* into *store*.

    Files whose content hash already matches the stored value are left
    untouched. When ``prune_missing`` is true, any document whose source
    file no longer exists is removed.
    """
    root = Path(root).resolve()
    excludes = _DEFAULT_EXCLUDES + extra_excludes

    scanned = indexed = unchanged = skipped = 0
    seen_paths: set[str] = set()

    for path in _iter_candidate_files(root, excludes):
        scanned += 1
        rel = path.relative_to(root).as_posix()
        suffix = path.suffix.lower()
        source_type = _classify(rel, suffix)
        if source_type is None:
            skipped += 1
            continue
        try:
            size = path.stat().st_size
        except OSError:
            skipped += 1
            continue
        if size > _MAX_FILE_BYTES:
            skipped += 1
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            skipped += 1
            continue
        chunks = _chunk_text(content)
        if not chunks:
            skipped += 1
            continue
        seen_paths.add(rel)
        changed = store.upsert_document(
            source_path=rel,
            source_type=source_type,
            title=path.name,
            content=content,
            chunks=chunks,
        )
        if changed:
            indexed += 1
        else:
            unchanged += 1

    removed = 0
    if prune_missing:
        for known in list(store.known_paths()):
            if known not in seen_paths:
                if store.forget(known):
                    removed += 1

    return IngestResult(
        scanned=scanned,
        indexed=indexed,
        unchanged=unchanged,
        removed=removed,
        skipped=skipped,
    )
