# export

## Overview
Provides a pluggable report-export subsystem based on the `OutputFormatter` ABC and a central `FORMATTER_REGISTRY`, supporting conversion of persisted `ReportRecord` instances into styled PDF (`fpdf2`) and self-contained HTML documents via programmatic APIs or standalone CLI entry points.

## Interface
- `[__init__.py:FORMATTER_REGISTRY]` - Dict mapping short format names (`'pdf'`, `'html'`) to their `OutputFormatter` classes (`PdfFormatter`, `HtmlFormatter`).
- `[__init__.py:get_formatter]` - Factory function `get_formatter(name: str) -> OutputFormatter` that instantiates a registered formatter by name or raises `KeyError`.
- `[formatter.py:DISCLAIMER]` - Shared AI-generated informational disclaimer string included in exported report footers.
- `[formatter.py:DATA_SOURCES]` - Shared data-source attribution string (`'Data Sources: DragonFi (api.dragonfi.ph), PSE EDGE (edge.pse.com.ph), TradingView Scanner, Tavily Web Search'`).
- `[formatter.py:format_timestamp]` - Function `format_timestamp(dt_val: datetime | None) -> str` converting a UTC `datetime` into the user's configured timezone with abbreviation, or returning `''` when `None`.
- `[formatter.py:parse_sections]` - Function `parse_sections(summary: str) -> list[tuple[str, str]]` splitting a consolidated markdown/text report summary into normalized `(title, body)` section tuples.
- `[formatter.py:OutputFormatter]` - Abstract base class defining abstract properties `file_extension`, `format_label`, `emoji`, abstract method `render(record: ReportRecord) -> bytes`, and concrete helper `write(record: ReportRecord, path: Path) -> None`.
- `[formatter.py:export_cli]` - Standalone CLI runner `export_cli(formatter: OutputFormatter) -> None` parsing `'symbol [--id N] [-o PATH]'` arguments, querying the report repository, and writing the rendered file.
- `[html.py:HtmlFormatter]` - `OutputFormatter` subclass (`.html`, `'HTML'`, `'🌐'`) that renders a `ReportRecord` into UTF-8 encoded self-contained HTML with embedded CSS and Markdown-to-HTML conversion.
- `[html.py:main]` - CLI entry point for `'ph-advisor-html'` that invokes `export_cli(HtmlFormatter())`.
- `[pdf.py:PdfFormatter]` - `OutputFormatter` subclass (`.pdf`, `'PDF'`, `'📄'`) that renders a `ReportRecord` into raw A4 PDF bytes using `fpdf2`.
- `[pdf.py:main]` - CLI entry point for `'ph-advisor-pdf'` that invokes `export_cli(PdfFormatter())`.

## Invariants
- `[__init__.py:get_formatter]` - Raises `KeyError` with sorted available format names if the requested format key is not in `FORMATTER_REGISTRY`.
- `[formatter.py:parse_sections]` - Defaults initial section title to `'Executive Summary'`; drops separator lines of 2+ dashes, standalone inline verdict lines (`'Verdict: BUY/NOT BUY'`), and any section whose body is empty after stripping.
- `[formatter.py:_neutralise_verdict_section]` - Retitles section headings matching `_VERDICT_TITLE` (`verdict`, `buy`, `strong buy`, `not buy`, `don't buy`, `wait`, `avoid`) to `'Why This Verdict'` (`_RATIONALE_TITLE`) and rewrites leading verdict subjects (`_LEADING_VERDICT_SUBJECT`) to `'This assessment <was|is>'`, or returns `None` if body is empty.
- `[formatter.py:_strip_title_from_body]` - Removes the first non-blank line of a section body if its normalized text matches the section title.
- `[formatter.py:OutputFormatter.file_extension]` - Must include the leading dot (e.g. `'.pdf'`, `'.html'`).
- `[formatter.py:export_cli]` - Normalizes the CLI `symbol` argument via `.upper().replace('.PS', '')` before querying the repository.
- `[html.py:_body_to_html]` - Bumps Markdown ATX heading levels by +2 (capped at `h6`, so `#` -> `h3`) to avoid clashing with page-level `h1`/`h2`, and assigns class `'metrics-table'` for <=2 columns or `'wide-table'` for >2 columns.
- `[html.py:HtmlFormatter.render]` - Skips any parsed section whose body is empty or whose title starts with `'verdict'` (case-insensitive).
- `[pdf.py:_sanitize]` - Replaces non-latin-1 characters (including `'\u20b1'` -> `'PHP '`) via `_UNICODE_SUBS` and encodes/decodes as `latin-1` with replacement so built-in PDF Helvetica fonts do not fail.
- `[pdf.py:PdfFormatter.render]` - Skips any parsed section whose body is empty or whose title starts with `'verdict'` (case-insensitive).

## Side Effects
- `[formatter.py:format_timestamp]` - Reads application settings via `get_settings().timezone`.
- `[formatter.py:OutputFormatter.write]` - Writes rendered bytes to the local filesystem via `Path.write_bytes`.
- `[formatter.py:export_cli]` - Parses `sys.argv` via `argparse`, opens and closes a database/storage connection via `get_repository()` / `repo.close()`, prints status/warnings to `stdout`, writes the output file to disk, and calls `sys.exit(1)` if no report record is found.
- `[html.py:_get_version]` - Queries installed package metadata for `'ph-stocks-advisor'` via `importlib.metadata.version` (falling back to `'dev'`).
- `[html.py:_CSS]` - Embeds an external CSS `@import` rule requesting Google Fonts (`'https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap'`) when the generated HTML is opened in a browser.

## Verification
- Run: `pytest`
