"""
PSE EDGE dividend disclosure scraper.

Fetches recent cash-dividend declarations from the PSE EDGE disclosure
system (``https://edge.pse.com.ph``).  The approach mirrors what a human
would do on the PSE EDGE website:

1. POST to ``/companyDisclosures/search.ax`` with template
   ``Declaration of Cash Dividends`` to get the latest filings.
2. For each filing, GET the viewer page to discover the ``file_id``.
3. GET ``/downloadHtml.do?file_id=…`` for the actual SEC Form 6-1 HTML.
4. Parse the HTML for stock symbol, amount per share, ex-date,
   record date, and payment date.

No API key required — all endpoints are public.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import requests

logger = logging.getLogger(__name__)


def _base_url() -> str:
    from ph_stocks_advisor.infra.config import get_settings
    return get_settings().pse_edge_base_url


def _timeout() -> int:
    from ph_stocks_advisor.infra.config import get_settings
    return get_settings().http_timeout


# ---------------------------------------------------------------------------
# Data transfer object
# ---------------------------------------------------------------------------

@dataclass
class DeclaredDividend:
    """A single cash-dividend declaration scraped from PSE EDGE."""

    symbol: str
    amount_per_share: Optional[str]
    ex_date: Optional[str]
    record_date: Optional[str]
    payment_date: Optional[str]
    announce_date: Optional[str] = None

    def to_summary(self) -> str:
        """Human-readable one-line summary for LLM consumption."""
        parts = [f"{self.symbol}"]
        if self.amount_per_share:
            parts.append(f"cash dividend of {self.amount_per_share}/share")
        if self.ex_date:
            parts.append(f"ex-date {self.ex_date}")
        if self.record_date:
            parts.append(f"record date {self.record_date}")
        if self.payment_date:
            parts.append(f"payment date {self.payment_date}")
        if self.announce_date:
            parts.append(f"(announced {self.announce_date})")
        return ", ".join(parts)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_DATE_RE = re.compile(r"[A-Za-z]{3}\s+\d{1,2},\s*\d{4}")


def _parse_disclosure_html(html: str) -> Optional[DeclaredDividend]:
    """Extract dividend fields from an SEC Form 6-1 HTML body."""
    # Flatten to searchable text
    text = re.sub(r"<[^>]+>", "\n", html)
    text = re.sub(r"\s+", " ", text).strip()

    # Stock symbol — appears right before "PSE Disclosure Form 6-1"
    sym_match = re.search(
        r"\b([A-Z0-9]{2,10})\s+PSE\s+Disclosure\s+Form\s+6-1", text
    )
    if not sym_match:
        return None
    symbol = sym_match.group(1)

    # Amount per share (various currency prefixes: P, Php, PHP, ₱)
    amt_match = re.search(
        r"Amount of Cash Dividend Per Share\s*[:\s]*((?:P(?:hp)?|PHP|₱)?\s*[\d,.]+)",
        text,
        re.IGNORECASE,
    )
    amount = amt_match.group(1).strip() if amt_match else None

    # Dates
    ex_match = re.search(r"Ex-Date\s*:\s*(" + _DATE_RE.pattern + ")", text)
    rec_match = re.search(r"Record Date\s*(" + _DATE_RE.pattern + ")", text)
    pay_match = re.search(r"Payment Date\s*(" + _DATE_RE.pattern + ")", text)

    return DeclaredDividend(
        symbol=symbol,
        amount_per_share=amount,
        ex_date=ex_match.group(1) if ex_match else None,
        record_date=rec_match.group(1) if rec_match else None,
        payment_date=pay_match.group(1) if pay_match else None,
    )


def _fetch_disclosure_content(edge_no: str) -> Optional[str]:
    """Fetch the raw HTML content of a PSE EDGE disclosure."""
    base = _base_url()
    try:
        # Step 1: viewer page → extract iframe file_id
        viewer = requests.get(
            f"{base}/openDiscViewer.do",
            params={"edge_no": edge_no},
            timeout=_timeout(),
        )
        if viewer.status_code != 200:
            return None

        iframe_match = re.search(
            r"/downloadHtml\.do\?file_id=(\d+)", viewer.text
        )
        if not iframe_match:
            return None

        # Step 2: download the actual HTML form
        content = requests.get(
            f"{base}/downloadHtml.do",
            params={"file_id": iframe_match.group(1)},
            timeout=_timeout(),
        )
        return content.text if content.status_code == 200 else None

    except requests.RequestException as exc:
        logger.warning("PSE EDGE disclosure fetch failed (%s): %s", edge_no, exc)
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_recent_dividend_declarations(
    symbol: str,
    *,
    max_disclosures: int = 50,
    max_matches: int = 3,
) -> list[DeclaredDividend]:
    """Return recent cash-dividend declarations for *symbol* from PSE EDGE.

    Scans the latest ``max_disclosures`` PSE-wide filings and returns up to
    ``max_matches`` that match the given stock symbol.

    Parameters
    ----------
    symbol : str
        PSE stock code (e.g. ``"TEL"``, ``"CREIT"``).
    max_disclosures : int
        How many recent filings to scan (the latest 50 by default).
    max_matches : int
        Stop after finding this many matching declarations.

    Returns
    -------
    list[DeclaredDividend]
        Matching declarations, newest first.  Empty list on any error.
    """
    symbol = symbol.upper().replace(".PS", "")
    base = _base_url()

    try:
        resp = requests.post(
            f"{base}/companyDisclosures/search.ax",
            data={
                "keyword": "",
                "tmplNm": "Declaration of Cash Dividends",
                "sortType": "date",
            },
            headers={"X-Requested-With": "XMLHttpRequest"},
            timeout=_timeout(),
        )
        if resp.status_code != 200:
            logger.warning(
                "PSE EDGE disclosure search returned %s", resp.status_code
            )
            return []
    except requests.RequestException as exc:
        logger.warning("PSE EDGE disclosure search failed: %s", exc)
        return []

    # Extract disclosure IDs + announce dates from the result table
    ids_dates: list[tuple[str, str]] = []
    rows = re.findall(r"<tr[^>]*>.*?</tr>", resp.text, re.DOTALL)
    for row in rows:
        id_match = re.search(r"openPopup\('([^']+)'\)", row)
        date_match = re.search(
            r"class=\"alignC\">\s*([A-Za-z]{3}\s+\d{1,2},\s*\d{4})", row
        )
        if id_match:
            announce = date_match.group(1) if date_match else None
            ids_dates.append((id_match.group(1), announce or ""))

    matches: list[DeclaredDividend] = []
    for edge_no, announce_date in ids_dates[:max_disclosures]:
        html = _fetch_disclosure_content(edge_no)
        if not html:
            continue
        parsed = _parse_disclosure_html(html)
        if parsed and parsed.symbol == symbol:
            parsed.announce_date = announce_date
            matches.append(parsed)
            if len(matches) >= max_matches:
                break

    if matches:
        logger.info(
            "Found %d PSE EDGE dividend declaration(s) for %s",
            len(matches),
            symbol,
        )
    else:
        logger.debug("No PSE EDGE dividend declarations found for %s", symbol)

    return matches
