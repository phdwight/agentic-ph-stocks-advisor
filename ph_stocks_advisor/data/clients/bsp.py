"""
Bangko Sentral ng Pilipinas (BSP) key interest rates client.

Fetches the official BSP key interest rates and macro indicators displayed on
``https://www.bsp.gov.ph/SitePages/Statistics/KeyRates.aspx``.

That SharePoint page renders its rate cards client-side via PnPJS from the
``Key Rates`` SharePoint list:
``/_api/web/lists/getByTitle('Key%20Rates')/items?$select=*&$orderby=Order0%20asc``

No API key required — the SharePoint REST endpoint is public.

Single Responsibility: only handles fetching and parsing the BSP Key Rates
SharePoint list.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import requests

from ph_stocks_advisor.data.models import BSPKeyRateItem, BSPKeyRates

logger = logging.getLogger(__name__)

_KEY_RATES_PAGE_PATH = "/SitePages/Statistics/KeyRates.aspx"
_KEY_RATES_API_PATH = "/_api/web/lists/getByTitle('Key%20Rates')/items"

_NUMBER_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")


def _base_url() -> str:
    from ph_stocks_advisor.infra.config import get_settings

    return get_settings().bsp_base_url.rstrip("/")


def _timeout() -> int:
    from ph_stocks_advisor.infra.config import get_settings

    return get_settings().http_timeout


def _parse_float(raw: str) -> float | None:
    """Extract the first floating-point number from *raw*, or ``None``."""
    if not raw or "*" in raw:
        return None
    match = _NUMBER_RE.search(raw.replace(",", ""))
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _parse_rate_pct(raw: str) -> float | None:
    """Parse a percentage string like ``'5.50%'`` into ``5.5``.

    Returns ``None`` when the value is not a percentage (e.g. ``'63.5590'``
    for the USD/PHP exchange rate or ``'****'`` for discontinued series).
    """
    if not raw or "%" not in raw:
        return None
    return _parse_float(raw)


def _extract_raw_items(payload: Any) -> list[dict[str, Any]]:
    """Extract the SharePoint item list from ``nometadata`` or ``verbose`` JSON."""
    if not isinstance(payload, dict):
        return []
    if isinstance(payload.get("value"), list):
        return [r for r in payload["value"] if isinstance(r, dict)]
    d_obj = payload.get("d")
    if isinstance(d_obj, dict) and isinstance(d_obj.get("results"), list):
        return [r for r in d_obj["results"] if isinstance(r, dict)]
    return []


def format_bsp_rates_summary(rates: list[BSPKeyRateItem]) -> str:
    """Format parsed BSP key rates into a concise human/LLM-readable string."""
    parts: list[str] = []
    for item in rates:
        if not item.value or "*" in item.value:
            continue
        parts.append(item.to_summary())
    return "; ".join(parts)


def _parse_key_rates_payload(payload: Any, *, base_url: str) -> BSPKeyRates:
    """Transform the SharePoint JSON payload into a :class:`BSPKeyRates` model."""
    raw_items = _extract_raw_items(payload)
    source_url = f"{base_url}{_KEY_RATES_PAGE_PATH}"
    if not raw_items:
        return BSPKeyRates(source_url=source_url)

    # Sort deterministically by SharePoint's Order0 column when present.
    raw_items.sort(key=lambda r: float(r["Order0"]) if isinstance(r.get("Order0"), int | float) else float("inf"))

    items: list[BSPKeyRateItem] = []
    target_rrp_rate: float | None = None
    overnight_lending_rate: float | None = None
    overnight_deposit_rate: float | None = None
    overnight_rrp_rate: float | None = None
    bsp_securities_28d_wair: float | None = None
    bsp_securities_56d_wair: float | None = None
    tdf_7d_wair: float | None = None
    tdf_14d_wair: float | None = None
    tdf_28d_wair: float | None = None
    inflation_rate: float | None = None
    usd_php_rate: float | None = None
    as_of_date = ""

    for raw in raw_items:
        title = str(raw.get("Title") or "").strip()
        value = str(raw.get("Value") or "").strip()
        if not title or not value:
            continue

        published_date = str(raw.get("Published_x0020_Date") or "").strip()
        yields_raw = raw.get("Yields")
        accepted_yields = str(yields_raw).strip() if yields_raw else None
        url = str(raw.get("URL") or "").strip()
        rate_pct = _parse_rate_pct(value)

        item = BSPKeyRateItem(
            title=title,
            value=value,
            rate_pct=rate_pct,
            published_date=published_date,
            accepted_yields=accepted_yields,
            url=url,
        )
        items.append(item)

        norm_title = title.lower()
        if norm_title == "target rrp rate":
            target_rrp_rate = rate_pct
            if published_date:
                as_of_date = published_date
        elif norm_title == "on lending rate":
            overnight_lending_rate = rate_pct
            if published_date and not as_of_date:
                as_of_date = published_date
        elif norm_title == "on deposit rate":
            overnight_deposit_rate = rate_pct
            if published_date and not as_of_date:
                as_of_date = published_date
        elif norm_title == "on rrp rate":
            overnight_rrp_rate = rate_pct
        elif norm_title == "28-day bsp securities (wair)":
            bsp_securities_28d_wair = rate_pct
        elif norm_title == "56-day bsp securities (wair)":
            bsp_securities_56d_wair = rate_pct
        elif norm_title == "7-day tdf (wair)":
            tdf_7d_wair = rate_pct
        elif norm_title == "14-day tdf (wair)":
            tdf_14d_wair = rate_pct
        elif norm_title == "28-day tdf (wair)":
            tdf_28d_wair = rate_pct
        elif norm_title.startswith("inflation rate"):
            inflation_rate = rate_pct
        elif norm_title == "us$ 1.00":
            usd_php_rate = _parse_float(value)

    return BSPKeyRates(
        target_rrp_rate=target_rrp_rate,
        overnight_lending_rate=overnight_lending_rate,
        overnight_deposit_rate=overnight_deposit_rate,
        overnight_rrp_rate=overnight_rrp_rate,
        bsp_securities_28d_wair=bsp_securities_28d_wair,
        bsp_securities_56d_wair=bsp_securities_56d_wair,
        tdf_7d_wair=tdf_7d_wair,
        tdf_14d_wair=tdf_14d_wair,
        tdf_28d_wair=tdf_28d_wair,
        inflation_rate=inflation_rate,
        usd_php_rate=usd_php_rate,
        as_of_date=as_of_date,
        source_url=source_url,
        rates=items,
        summary=format_bsp_rates_summary(items),
    )


def fetch_bsp_key_rates() -> BSPKeyRates:
    """Fetch current BSP key interest rates from the official BSP SharePoint API.

    Queries the ``Key Rates`` SharePoint list that backs
    ``https://www.bsp.gov.ph/SitePages/Statistics/KeyRates.aspx`` and returns
    a populated :class:`BSPKeyRates` model. Returns an empty :class:`BSPKeyRates`
    instance on network or parsing errors without raising.
    """
    base = _base_url()
    source_url = f"{base}{_KEY_RATES_PAGE_PATH}"
    url = f"{base}{_KEY_RATES_API_PATH}"

    try:
        resp = requests.get(
            url,
            params={"$select": "*", "$orderby": "Order0 asc"},
            headers={"Accept": "application/json;odata=nometadata"},
            timeout=_timeout(),
        )
        if resp.status_code != 200:
            logger.warning("BSP Key Rates API returned HTTP %s from %s", resp.status_code, url)
            return BSPKeyRates(source_url=source_url)

        payload = resp.json()
        result = _parse_key_rates_payload(payload, base_url=base)
        if result.rates:
            logger.info(
                "Fetched %d BSP key rate entry/entries (target_rrp=%s%%, as_of=%s)",
                len(result.rates),
                result.target_rrp_rate,
                result.as_of_date,
            )
        else:
            logger.warning("BSP Key Rates API returned no rate entries from %s", url)
        return result
    except Exception as exc:
        logger.warning("BSP Key Rates fetch failed: %s", exc)
        return BSPKeyRates(source_url=source_url)
