"""
PSE EDGE company-page dividend scraper.

Fetches the dividend history table from a company's PSE EDGE dividends page
(``https://edge.pse.com.ph/companyPage/dividends_and_rights_form.do?cmpy_id=…``).

This is a **different** endpoint from the disclosure-based scraper in
``pse_edge_dividends.py`` — that one scans SEC Form 6-1 filings; this one
scrapes the structured HTML table that lists all declared dividends for a
single company.

Two-step process:

1. Resolve the ``cmpy_id`` for the stock symbol via the autocomplete API
   (reuses logic from ``pse_edge.py``).
2. POST to ``/companyPage/dividends_and_rights_list.ax`` to get the HTML
   table, then parse each ``<tr>`` row into a ``DividendAnnouncement``.

No API key required — all endpoints are public.

Single Responsibility: only handles company-page dividend table scraping.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import requests

from ph_stocks_advisor.data.models import DividendAnnouncement

logger = logging.getLogger(__name__)


def _base_url() -> str:
    from ph_stocks_advisor.infra.config import get_settings
    return get_settings().pse_edge_base_url


def _timeout() -> int:
    from ph_stocks_advisor.infra.config import get_settings
    return get_settings().http_timeout


# ---------------------------------------------------------------------------
# cmpy_id resolution (mirrors pse_edge.py but avoids coupling)
# ---------------------------------------------------------------------------

_CMPY_ID_CACHE: dict[str, str] = {}


def _resolve_cmpy_id(symbol: str) -> Optional[str]:
    """Look up the PSE EDGE ``cmpy_id`` for a ticker symbol."""
    symbol = symbol.upper()
    if symbol in _CMPY_ID_CACHE:
        return _CMPY_ID_CACHE[symbol]

    try:
        resp = requests.get(
            f"{_base_url()}/autoComplete/searchCompanyNameSymbol.ax",
            params={"term": symbol},
            headers={"X-Requested-With": "XMLHttpRequest"},
            timeout=_timeout(),
        )
        if resp.status_code != 200:
            logger.debug(
                "PSE EDGE autocomplete returned %s for %s",
                resp.status_code, symbol,
            )
            return None

        for item in resp.json():
            if item.get("symbol", "").upper() == symbol:
                cmpy_id = str(item["cmpyId"])
                _CMPY_ID_CACHE[symbol] = cmpy_id
                return cmpy_id

        logger.debug("PSE EDGE autocomplete: no exact match for %s", symbol)
        return None

    except Exception as exc:
        logger.warning("PSE EDGE autocomplete failed for %s: %s", symbol, exc)
        return None


# ---------------------------------------------------------------------------
# HTML table row parser
# ---------------------------------------------------------------------------

_STRIP_TAGS_RE = re.compile(r"<[^>]+>")


def _strip_tags(html: str) -> str:
    """Remove HTML tags, returning clean text."""
    return _STRIP_TAGS_RE.sub("", html).strip()


def _parse_dividend_rows(html: str) -> list[DividendAnnouncement]:
    """Parse ``<tr>`` rows from the dividends table into model objects.

    Expected columns (0-indexed):
        0 — Type of Security  (e.g. COMMON)
        1 — Type of Dividend  (e.g. Cash)
        2 — Dividend Rate     (e.g. Php0.62)
        3 — Ex-Dividend Date  (e.g. Mar 04, 2026)
        4 — Record Date       (e.g. Mar 5, 2026)
        5 — Payment Date      (e.g. Mar 20, 2026)
        6 — Circular Number   (e.g. C01040-2026)
    """
    results: list[DividendAnnouncement] = []

    # Find <tbody> section to skip the header row
    tbody_match = re.search(r"<tbody>(.*?)</tbody>", html, re.DOTALL)
    if not tbody_match:
        return results

    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", tbody_match.group(1), re.DOTALL)
    for row_html in rows:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.DOTALL)
        if len(cells) < 6:
            continue

        security_type = _strip_tags(cells[0])
        dividend_type = _strip_tags(cells[1])
        dividend_rate = _strip_tags(cells[2])
        ex_date = _strip_tags(cells[3])
        record_date = _strip_tags(cells[4])
        payment_date = _strip_tags(cells[5])
        circular_number = _strip_tags(cells[6]) if len(cells) > 6 else ""

        if not dividend_rate or not ex_date:
            continue

        results.append(
            DividendAnnouncement(
                security_type=security_type,
                dividend_type=dividend_type,
                dividend_rate=dividend_rate,
                ex_date=ex_date,
                record_date=record_date,
                payment_date=payment_date,
                circular_number=circular_number,
            )
        )

    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fetch_company_dividend_announcements(
    symbol: str,
    *,
    max_results: int = 5,
) -> list[DividendAnnouncement]:
    """Fetch dividend announcements from the PSE EDGE company dividends page.

    Returns up to *max_results* most recent dividend announcements as
    structured ``DividendAnnouncement`` objects containing ex-date,
    dividend rate, and payment date.

    Parameters
    ----------
    symbol : str
        PSE stock code (e.g. ``"AREIT"``, ``"TEL"``).
    max_results : int
        Maximum number of announcements to return (default: 5).

    Returns
    -------
    list[DividendAnnouncement]
        Announcements newest-first.  Empty list on any error.
    """
    symbol = symbol.upper().replace(".PS", "")

    cmpy_id = _resolve_cmpy_id(symbol)
    if not cmpy_id:
        logger.warning(
            "Could not resolve cmpy_id for %s — skipping company dividend page",
            symbol,
        )
        return []

    try:
        resp = requests.post(
            f"{_base_url()}/companyPage/dividends_and_rights_list.ax",
            params={"DividendsOrRights": "Dividends"},
            data={"cmpy_id": cmpy_id},
            headers={"X-Requested-With": "XMLHttpRequest"},
            timeout=_timeout(),
        )
        if resp.status_code != 200:
            logger.warning(
                "PSE EDGE company dividends returned %s for %s (cmpy_id=%s)",
                resp.status_code, symbol, cmpy_id,
            )
            return []
    except requests.RequestException as exc:
        logger.warning(
            "PSE EDGE company dividends fetch failed for %s: %s", symbol, exc,
        )
        return []

    announcements = _parse_dividend_rows(resp.text)

    if announcements:
        logger.info(
            "Found %d dividend announcement(s) for %s from company page",
            len(announcements), symbol,
        )
    else:
        logger.debug(
            "No dividend announcements found on company page for %s", symbol,
        )

    return announcements[:max_results]
