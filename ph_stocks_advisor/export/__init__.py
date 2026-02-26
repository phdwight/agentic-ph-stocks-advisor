"""
Export package — pluggable report-output formatters.

Subpackage layout::

    export/
    ├── formatter.py   # OutputFormatter ABC, parse_sections(), export_cli()
    ├── pdf.py         # PdfFormatter  (fpdf2)
    └── html.py        # HtmlFormatter (pure-Python)

**Adding a new format** requires three steps:

1. Create a new module with a class that subclasses ``OutputFormatter``.
2. Register it in :data:`FORMATTER_REGISTRY` below.
3. Add a ``--<fmt>`` flag in ``main.py`` and a CLI entry in ``pyproject.toml``.
"""

from __future__ import annotations

from ph_stocks_advisor.export.formatter import (
    OutputFormatter,
    export_cli,
    parse_sections,
)
from ph_stocks_advisor.export.html import HtmlFormatter
from ph_stocks_advisor.export.pdf import PdfFormatter

__all__ = [
    "OutputFormatter",
    "PdfFormatter",
    "HtmlFormatter",
    "export_cli",
    "parse_sections",
    "get_formatter",
    "FORMATTER_REGISTRY",
]


# ---------------------------------------------------------------------------
# Registry — maps short names to formatter classes
# ---------------------------------------------------------------------------

FORMATTER_REGISTRY: dict[str, type[OutputFormatter]] = {
    "pdf": PdfFormatter,
    "html": HtmlFormatter,
}
"""Mapping of format name → formatter class. Used by ``main.py`` to resolve
the ``--pdf`` / ``--html`` flags into concrete formatters."""


def get_formatter(name: str) -> OutputFormatter:
    """Instantiate a formatter by its registered short name.

    Raises :class:`KeyError` with a helpful message when the name is unknown.
    """
    try:
        cls = FORMATTER_REGISTRY[name]
    except KeyError:
        available = ", ".join(sorted(FORMATTER_REGISTRY))
        raise KeyError(
            f"Unknown output format {name!r}. Available: {available}"
        ) from None
    return cls()
