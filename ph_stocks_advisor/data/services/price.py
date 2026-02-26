"""
Price data service — fetches current price snapshots for PSE stocks.

Single Responsibility: only handles price data retrieval and catalyst detection.
"""

from __future__ import annotations

import logging
from typing import Any

from ph_stocks_advisor.data.clients.dragonfi import fetch_stock_profile
from ph_stocks_advisor.data.models import StockPrice
from ph_stocks_advisor.infra.config import get_settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Price catalyst detection
# ---------------------------------------------------------------------------

def detect_price_catalysts(profile: dict[str, Any]) -> list[str]:
    """Infer likely price catalysts from DragonFi profile data.

    Cross-references dividend yield, REIT status, and price position
    relative to the 52-week range to identify what may be driving price.
    """
    catalysts: list[str] = []
    if not profile:
        return catalysts

    s = get_settings()
    div_yield = float(profile.get("dividendYield", 0) or 0)
    is_reit = bool(profile.get("isREIT", False))
    price = float(profile.get("price", 0) or 0)
    high52 = float(profile.get("weekHigh52", 0) or 0)
    low52 = float(profile.get("weekLow52", 0) or 0)
    prev_close = float(profile.get("prevDayClosePrice", 0) or 0)

    # How close is the price to the 52-week high? (0-100%)
    range_52 = high52 - low52
    pct_of_range = ((price - low52) / range_52 * 100) if range_52 > 0 else 50.0

    # Detect dividend-driven price movement
    if div_yield > s.catalyst_yield_threshold and pct_of_range > s.catalyst_range_pct:
        if is_reit:
            catalysts.append(
                f"REIT with {div_yield:.1f}% dividend yield trading in the upper "
                f"portion of its 52-week range — price is likely being driven by "
                f"investors accumulating shares ahead of the next dividend payout "
                f"(\"dividend play\"). Philippine REITs distribute dividends quarterly."
            )
        else:
            catalysts.append(
                f"High-dividend stock ({div_yield:.1f}% yield) trading near its "
                f"52-week high — the upward price movement may be driven by "
                f"investors buying ahead of an expected dividend declaration."
            )

    # Detect recent upward momentum vs. previous close
    if prev_close > 0 and price > prev_close:
        day_change_pct = ((price - prev_close) / prev_close) * 100
        if day_change_pct > s.catalyst_day_change_pct and div_yield > s.catalyst_yield_threshold:
            catalysts.append(
                f"Price rose {day_change_pct:.2f}% from the previous close, "
                f"which may reflect continued demand from dividend-seeking investors."
            )

    # Approaching 52-week high
    if high52 > 0 and price > 0:
        gap_to_high = ((high52 - price) / high52) * 100
        if gap_to_high < s.catalyst_near_high_pct:
            catalysts.append(
                f"Price is within {gap_to_high:.1f}% of its 52-week high "
                f"(₱{high52}), indicating strong buying pressure."
            )

    return catalysts


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_stock_price(symbol: str) -> StockPrice:
    """Fetch current price snapshot for a PSE-listed stock.

    Source: DragonFi API. Returns a minimal object when data is unavailable.
    """
    symbol = symbol.upper().replace(".PS", "")
    profile = fetch_stock_profile(symbol)

    if profile and profile.get("price"):
        catalysts = detect_price_catalysts(profile)
        return StockPrice(
            symbol=symbol,
            current_price=float(profile.get("price", 0)),
            currency="PHP",
            fifty_two_week_high=float(profile.get("weekHigh52", 0) or 0),
            fifty_two_week_low=float(profile.get("weekLow52", 0) or 0),
            previous_close=float(profile.get("prevDayClosePrice", 0) or 0),
            price_catalysts=catalysts,
        )

    logger.warning("DragonFi returned no price data for %s", symbol)
    return StockPrice(symbol=symbol, current_price=0.0, currency="PHP")
