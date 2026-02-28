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
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
:root{--bg:#1a1e2e;--card:rgba(34,39,58,0.80);--text:#f0f1f5;--text-sec:#b4b9cc;
  --text-muted:#828699;--accent:#6c63ff;--emerald:#34d399;--crimson:#f87171;
  --border:rgba(255,255,255,0.10);--radius:12px}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Inter',-apple-system,BlinkMacSystemFont,sans-serif;background:var(--bg);
  color:var(--text);line-height:1.65;letter-spacing:-0.01em;padding:2rem;
  -webkit-font-smoothing:antialiased}
.container{max-width:820px;margin:0 auto;overflow:hidden}
header{background:rgba(34,39,58,0.85);backdrop-filter:blur(16px);
  border:1px solid rgba(255,255,255,0.08);border-radius:16px;
  padding:2rem 2.5rem 1.5rem;margin-bottom:1.5rem}
header h1{font-size:1.75rem;font-weight:800;letter-spacing:-0.03em;margin-bottom:.35rem}
header .meta{font-size:.8rem;color:var(--text-muted)}
.verdict-row{margin-top:.6rem;display:flex;align-items:center;gap:.5rem}
.verdict-label{font-size:.85rem;font-weight:600;color:var(--text-sec);
  text-transform:lowercase;letter-spacing:0.03em}
.badge{display:inline-block;padding:.35rem 1.3rem;border-radius:999px;font-weight:700;
  font-size:.85rem;color:#fff;letter-spacing:0.04em}
.badge.buy{background:var(--emerald);box-shadow:0 2px 12px rgba(52,211,153,0.3)}
.badge.not-buy{background:var(--crimson);box-shadow:0 2px 12px rgba(248,113,113,0.3)}
main{display:flex;flex-direction:column;gap:1.2rem}
section{background:var(--card);backdrop-filter:blur(16px);
  border:1px solid rgba(255,255,255,0.08);border-radius:16px;padding:2rem 2.2rem}
section h2{font-size:.95rem;font-weight:700;color:var(--text);letter-spacing:-0.01em;
  padding-bottom:.6rem;margin-bottom:1rem;border-bottom:1px solid var(--border)}
section p,section li{font-size:.88rem;color:var(--text-sec);line-height:1.75}
section p{margin-bottom:.6rem}
section ul{padding-left:1.2rem;margin:.4rem 0 .6rem}
section li{margin-bottom:.35rem;padding-left:.3rem}
section strong{font-weight:600;color:var(--text)}
footer{text-align:center;padding:2rem 0;font-size:.7rem;color:var(--text-muted);
  border-top:1px solid var(--border);margin-top:1.5rem;line-height:1.6}
footer .disclaimer{margin-bottom:.3rem}
footer .sources{font-style:italic;opacity:.7}
@media print{body{padding:0;background:#fff;color:#222}
  section{background:#fff;border:1px solid #ddd}
  header{background:#f5f5f5;border:1px solid #ddd}
  .badge.buy{background:#228b22}.badge.not-buy{background:#c83232}}
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
