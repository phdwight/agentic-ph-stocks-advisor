"""
Output formatter abstraction and shared utilities.

Provides :class:`OutputFormatter`, the base class that all export formats
(PDF, HTML, …) must implement, plus shared helpers such as
:func:`parse_sections` and the generic :func:`export_cli` entry point.

**Open/Closed Principle** — new formats are added by subclassing
``OutputFormatter`` and registering the class; existing code is unchanged.

**Dependency Inversion** — consumers depend on the ``OutputFormatter``
abstraction, never on a concrete format implementation.
"""

from __future__ import annotations

import abc
import re
import sys
from pathlib import Path

from ph_stocks_advisor.infra.config import get_repository
from ph_stocks_advisor.infra.repository import ReportRecord


# ---------------------------------------------------------------------------
# Shared summary parser (used by every formatter)
# ---------------------------------------------------------------------------

def parse_sections(summary: str) -> list[tuple[str, str]]:
    """Split a consolidated summary into ``(title, body)`` pairs.

    Recognises two heading patterns produced by the consolidator:

    * ``**Title:**`` on its own line
    * ``**Title:** inline content``

    Lines containing only ``---`` are silently dropped.
    """
    sections: list[tuple[str, str]] = []
    current_title = "Executive Summary"
    current_lines: list[str] = []

    for line in summary.splitlines():
        stripped = line.strip()

        if stripped == "---":
            continue

        # Heading on its own line:  **Price Analysis:**
        heading_match = re.match(r"^\*\*(.+?):\*\*\s*$", stripped)
        if heading_match:
            if current_lines:
                sections.append((current_title, "\n".join(current_lines)))
                current_lines = []
            current_title = heading_match.group(1)
            continue

        # Heading with inline body:  **Price Analysis:** The price is …
        heading_inline = re.match(r"^\*\*(.+?):\*\*\s+(.+)$", stripped)
        if heading_inline:
            if current_lines:
                sections.append((current_title, "\n".join(current_lines)))
                current_lines = []
            current_title = heading_inline.group(1)
            current_lines.append(heading_inline.group(2))
            continue

        current_lines.append(line)

    if current_lines:
        sections.append((current_title, "\n".join(current_lines)))

    return sections


# ---------------------------------------------------------------------------
# Abstract base class
# ---------------------------------------------------------------------------

class OutputFormatter(abc.ABC):
    """Base class every report-export format must implement.

    Subclasses provide format-specific rendering; shared behaviour
    (file writing, CLI scaffold) is handled here.
    """

    @property
    @abc.abstractmethod
    def file_extension(self) -> str:
        """File extension **including the dot** (e.g. ``'.pdf'``)."""

    @property
    @abc.abstractmethod
    def format_label(self) -> str:
        """Human-readable label shown in CLI output (e.g. ``'PDF'``)."""

    @property
    @abc.abstractmethod
    def emoji(self) -> str:
        """Single emoji used to prefix CLI success messages."""

    @abc.abstractmethod
    def render(self, record: ReportRecord) -> bytes:
        """Render *record* into the target format and return raw bytes.

        For text-based formats (HTML, Markdown, …) return
        ``text.encode("utf-8")``.
        """

    # -- concrete helpers ----------------------------------------------------

    def write(self, record: ReportRecord, path: Path) -> None:
        """Render *record* and write the result to *path*."""
        path.write_bytes(self.render(record))


# ---------------------------------------------------------------------------
# Generic CLI entry point (reusable by every format)
# ---------------------------------------------------------------------------

def export_cli(formatter: OutputFormatter) -> None:
    """Standalone CLI that fetches a saved report and exports it.

    Provides a consistent ``symbol [--id N] [-o PATH]`` interface
    regardless of the output format.
    """
    import argparse

    parser = argparse.ArgumentParser(
        description=f"Export a saved stock report as {formatter.format_label}.",
    )
    parser.add_argument("symbol", help="Stock symbol (e.g. MREIT)")
    parser.add_argument(
        "--id", type=int, default=None,
        help="Specific report ID (default: latest)",
    )
    parser.add_argument(
        "-o", "--output", type=str, default=None,
        help=f"Output path (default: <SYMBOL>_report{formatter.file_extension})",
    )
    args = parser.parse_args()

    symbol = args.symbol.upper().replace(".PS", "")

    repo = get_repository()
    try:
        if args.id:
            record = repo.get_by_id(args.id)
            if record and record.symbol != symbol:
                print(
                    f"⚠️  Report id={args.id} is for {record.symbol}, not {symbol}"
                )
        else:
            record = repo.get_latest_by_symbol(symbol)
    finally:
        repo.close()

    if record is None:
        print(f"❌ No report found for {symbol}.")
        sys.exit(1)

    print(
        f"{formatter.emoji} Exporting report id={record.id} for {record.symbol} "
        f"(verdict: {record.verdict}, date: {record.created_at})…"
    )

    out_path = Path(args.output or f"{symbol}_report{formatter.file_extension}")
    formatter.write(record, out_path)
    print(f"✅ {formatter.format_label} saved to {out_path}")
