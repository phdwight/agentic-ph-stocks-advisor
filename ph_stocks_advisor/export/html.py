"""
HTML output formatter.

Renders a :class:`~ph_stocks_advisor.infra.repository.ReportRecord` as a
self-contained, responsive HTML page with embedded CSS.
"""

from __future__ import annotations

import html as _html
import re

from ph_stocks_advisor.export.formatter import OutputFormatter, parse_sections, DISCLAIMER, DATA_SOURCES, format_timestamp
from ph_stocks_advisor.infra.repository import ReportRecord


# ---------------------------------------------------------------------------
# CSS
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
.verdict-row{margin-top:.6rem;display:flex;align-items:center;gap:.5rem}
.verdict-label{font-size:1rem;font-weight:700;color:#fff}
.badge{display:inline-block;padding:.3rem 1.2rem;border-radius:999px;font-weight:700;
  font-size:1rem;color:#fff}
.badge.buy{background:var(--green)}.badge.not-buy{background:var(--red)}
main{padding:2rem 2.5rem 2.5rem}
section{margin-bottom:1.8rem}
section h2{font-size:1.15rem;color:var(--accent);border-bottom:2px solid var(--accent);
  padding-bottom:.25rem;margin-bottom:.6rem}
section p,section li{font-size:.95rem}
section ul{padding-left:1.4rem;margin-top:.3rem}
section li{margin-bottom:.25rem}
footer{text-align:center;padding:1.2rem 2.5rem;font-size:.75rem;color:#888;border-top:1px solid #eee;line-height:1.5}
footer .disclaimer{margin-bottom:.3rem}
footer .sources{font-style:italic}
@media print{body{padding:0}
  .container{box-shadow:none;border-radius:0}}
"""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _esc(text: str) -> str:
    """HTML-escape text."""
    return _html.escape(text, quote=True)


def _md_bold_to_html(text: str) -> str:
    """Convert ``**bold**`` markers to ``<strong>``."""
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)


def _body_to_html(body: str) -> str:
    """Convert a plain-text / light-markdown section body to HTML tags."""
    parts: list[str] = []
    in_list = False

    for raw_line in body.strip().splitlines():
        line = raw_line.strip()
        # Strip trailing dashes the LLM sometimes appends
        line = re.sub(r"-{2,}\s*$", "", line)
        if not line:
            if in_list:
                parts.append("</ul>")
                in_list = False
            parts.append("<p></p>")
            continue

        if line.startswith("- ") or line.startswith("* "):
            if not in_list:
                parts.append("<ul>")
                in_list = True
            bullet_text = re.sub(r"^([-*]\s+)+", "", line[2:]).strip()
            parts.append(f"<li>{_md_bold_to_html(_esc(bullet_text))}</li>")
        else:
            if in_list:
                parts.append("</ul>")
                in_list = False
            parts.append(f"<p>{_md_bold_to_html(_esc(line))}</p>")

    if in_list:
        parts.append("</ul>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Formatter
# ---------------------------------------------------------------------------

class HtmlFormatter(OutputFormatter):
    """Renders stock-analysis reports as self-contained HTML pages."""

    @property
    def file_extension(self) -> str:
        return ".html"

    @property
    def format_label(self) -> str:
        return "HTML"

    @property
    def emoji(self) -> str:
        return "🌐"

    def render(self, record: ReportRecord) -> bytes:  # noqa: D401
        """Build a complete HTML document and return UTF-8 bytes."""
        is_buy = record.verdict.upper() == "BUY"
        badge_cls = "buy" if is_buy else "not-buy"
        ts = format_timestamp(record.created_at)

        sections_html: list[str] = []
        for title, body in parse_sections(record.summary or ""):
            body = body.strip()
            if not body or title.lower().startswith("verdict"):
                continue
            sections_html.append(
                f'<section>\n<h2>{_esc(title)}</h2>\n{_body_to_html(body)}\n</section>'
            )

        html_str = f"""\
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
  <div class="verdict-row"><span class="verdict-label">Verdict:</span> <span class="badge {badge_cls}">{_esc(record.verdict)}</span></div>
  <div class="meta">Generated: {_esc(ts)}</div>
</header>
<main>
{chr(10).join(sections_html)}
</main>
<footer>
  <div class="disclaimer">{_esc(DISCLAIMER)}</div>
  <div class="sources">{_esc(DATA_SOURCES)}</div>
</footer>
</div>
</body>
</html>
"""
        return html_str.encode("utf-8")


# ---------------------------------------------------------------------------
# Standalone CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """``ph-advisor-html`` CLI — delegates to the shared :func:`export_cli`."""
    from ph_stocks_advisor.export.formatter import export_cli

    export_cli(HtmlFormatter())


if __name__ == "__main__":
    main()
