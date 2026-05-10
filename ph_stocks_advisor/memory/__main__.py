"""CLI for the long-term project memory.

Usage::

    ph-advisor-memory rebuild [--root .] [--db .copilot-memory.db]
    ph-advisor-memory update  [--root .] [--db .copilot-memory.db]
    ph-advisor-memory query "how does the consolidator work?" [-k 5] [--type rule]
    ph-advisor-memory stats   [--db .copilot-memory.db]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from ph_stocks_advisor.memory.embeddings import Embedder, OpenAIEmbedder

load_dotenv()
from ph_stocks_advisor.memory.ingest import ingest_project
from ph_stocks_advisor.memory.vector_store import VectorMemory

DEFAULT_DB = ".copilot-memory.db"


def _build_embedder() -> Embedder:
    return OpenAIEmbedder()


def _open_store(db_path: str) -> VectorMemory:
    return VectorMemory(db_path, _build_embedder())


def _cmd_rebuild(args: argparse.Namespace) -> int:
    db = Path(args.db)
    if db.exists():
        db.unlink()
    with _open_store(args.db) as store:
        result = ingest_project(args.root, store)
    print(json.dumps(result.__dict__, indent=2))
    return 0


def _cmd_update(args: argparse.Namespace) -> int:
    with _open_store(args.db) as store:
        result = ingest_project(args.root, store)
    print(json.dumps(result.__dict__, indent=2))
    return 0


def _cmd_query(args: argparse.Namespace) -> int:
    with _open_store(args.db) as store:
        hits = store.search(args.query, k=args.k, source_type=args.type)
    payload = [
        {
            "source_path": h.source_path,
            "source_type": h.source_type,
            "distance": round(h.distance, 4),
            "text": h.text,
        }
        for h in hits
    ]
    print(json.dumps(payload, indent=2))
    return 0


def _cmd_stats(args: argparse.Namespace) -> int:
    with _open_store(args.db) as store:
        print(json.dumps(store.stats(), indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ph-advisor-memory")
    parser.add_argument("--db", default=DEFAULT_DB, help="Path to the SQLite vector DB")
    sub = parser.add_subparsers(dest="cmd", required=True)

    rebuild = sub.add_parser("rebuild", help="Drop and rebuild the DB from scratch")
    rebuild.add_argument("--root", default=".", help="Project root to index")
    rebuild.set_defaults(func=_cmd_rebuild)

    update = sub.add_parser("update", help="Incrementally re-index changed files")
    update.add_argument("--root", default=".", help="Project root to index")
    update.set_defaults(func=_cmd_update)

    query = sub.add_parser("query", help="Semantic search the memory")
    query.add_argument("query")
    query.add_argument("-k", type=int, default=5, help="Number of hits")
    query.add_argument(
        "--type",
        choices=["code", "rule", "skill", "doc"],
        default=None,
        help="Restrict to a source type",
    )
    query.set_defaults(func=_cmd_query)

    stats = sub.add_parser("stats", help="Show document & chunk counts")
    stats.set_defaults(func=_cmd_stats)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
