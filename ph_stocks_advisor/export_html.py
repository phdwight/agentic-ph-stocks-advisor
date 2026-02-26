"""
Export a saved stock-analysis report as a styled HTML file.

Usage (standalone):
    python -m ph_stocks_advisor.export_html MREIT          # latest report
    python -m ph_stocks_advisor.export_html MREIT --id 26  # specific report id
    python -m ph_stocks_advisor.export_html MREIT -o ~/Desktop/MREIT.html
"""

from __future__ import annotations

import html
import re
import sys
from pathlib import Path

from ph_stocks_advisor.infra.config import get_repository
from ph_stocks_advisor.infra.repository import ReportRecord


# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------

_CSS = """\
:root{--accent:#1e3c78;--green:#228b22;--red:#c83232;--bg:#f8f9fb;--card:#fff}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:"Segoe UI",Roboto,Helvetica,Arial,sans-serif;background:var(--bg);
  color:#222;line-height:1.6;padding:2rem}
.container{max-width:820px;margin:0 auto;background:var(--card);border-radius:12px;
  box-shadow:0 2px 12px rgba(0,0,0,.08);overflow:hidden}
header{background:var(--accent);color:#fff;padding:2rem 2.5rem 1.5rem}
header h1{font-size:1.75rem;margin-bottom:.35rem}
header .meta{font-size:.85rem;opacity:.8}
.badge{display:inline-block;padding:.3rem 1rem;border-radius:6px;font-weight:700;
  font-size:1rem;margin-top:.6rem;color:#fff}
.badge.buy{background:var(--green)}.badge.not-buy{background:var(--red)}
main{padding:2rem 2.5rem 2.5rem}
section{margin-bottom:1.8rem}
section h2{font-size:1.15rem;color:var(--accent);border-bottom:2px solid var(--accent);
  padding-bottom:.25rem;margin-bottom:.6rem}
section p,section li{font-size:.95rem}
section ul{padding-left:1.4rem;margin-top:.3rem}
section li{margin-bottom:.25rem}
footer{text-align:center;padding:1rem;font-size:.75rem;color:#aaa;border-top:1px solid #eee}
@media print{body{padding:0}
  .container{box-shadow:none;border-radius:0}}
"""


def _esc(text: str) -> str:
    """HTML-escape text."""
    return html.escape(text, quote=True)


def _md_bold_to_html(text: str) -> str:
    """Convert **bold** markers to <strong>."""
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)


def _body_to_html(body: str) -> str:
    """Convert a plain-text / light-markdown section body to HTML."""
    parts: list[str] = []
    in_list = False

    for raw_line in body.strip().splitlines():
        line = raw_line.strip()
        if not line:
            if in_list:
                parts.append("</ul>")
                in_list = False
            parts.append("<p></p>")
            continue

        # Bullet point
        if line.startswith("- ") or line.startswith("* "):
            if not in_list:
                parts.append("<ul>")
                in_list = True
            parts.append(f"<li>{_md_bold_to_html(_esc(line[2:].strip()))}</li>")
        else:
            if in_list:
                parts.append("</ul>")
                in_list = False
            parts.append(f"<p>{_md_bold_to_html(_esc(line))}</p>")

    if in_list:
        parts.append("</ul>")
    return "\n".join(parts)


def _parse_sections(summary: str) -> list[tuple[str, str]]:
    """Split the summary into (title, body) sections."""
    sections: list[tuple[str, str]] = []
    current_title = "Executive Summary"
    current_lines: list[str] = []

    for line in summary.splitlines():
        stripped = line.strip()
        if stripped == "---":
            continue

        heading_match = re.match(r"^\*\*(.+?):\*\*\s*$", stripped)
        if heading_match:
            if current_lines:
                sections.append((current_title, "\n".join(current_lines)))
                current_lines = []
            current_title = heading_match.group(1)
            continue

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
# Public API
# ---------------------------------------------------------------------------

def build_html(record: ReportRecord) -> str:
    """Build a complete HTML document from a ReportRecord."""
    is_buy = record.verdict.upper() == "BUY"
    badge_cls = "buy" if is_buy else "not-buy"
    ts = (
        record.created_at.strftime("%B %d, %Y %I:%M %p")
        if record.created_at
        else ""
    )

    sections_html: list[str] = []
    for title, body in _parse_sections(record.summary or ""):
        body = body.strip()
        if not body or title.lower().startswith("verdict"):
            continue
        sections_html.append(
            f'<section>\n<h2>{_esc(title)}</h2>\n{_body_to_html(body)}\n</section>'
        )

    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(record.symbol)} Stock Analysis</title>
<style>{_CSS}</style>
</head>
<body>
<div class="container">
<header>
  <h1>{_esc(record.symbol)} Stock Analysis</h1>
  <span class="badge {badge_cls}">Verdict: {_esc(record.verdict)}</span>
  <div class="meta">Generated: {_esc(ts)}</div>
</header>
<main>
{chr(10).join(sections_html)}
</main>
<footer>Philippine Stock Advisor</footer>
</div>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Export a saved stock report as HTML.",
    )
    parser.add_argument("symbol", help="Stock symbol (e.g. MREIT)")
    parser.add_argument(
        "--id", type=int, default=None,
        help="Specific report ID (default: latest)",
    )
    parser.add_argument(
        "-o", "--output", type=str, default=None,
        help="Output HTML path (default: <SYMBOL>_report.html)",
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
        f"🌐 Exporting report id={record.id} for {record.symbol} "
        f"(verdict: {record.verdict}, date: {record.created_at})…"
    )

    html_str = build_html(record)

    out_path = args.output or f"{symbol}_report.html"
    Path(out_path).write_text(html_str, encoding="utf-8")
    print(f"✅ HTML saved to {out_path}")


if __name__ == "__main__":
    main()
