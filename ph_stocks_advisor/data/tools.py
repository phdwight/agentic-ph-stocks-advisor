"""
Market data tools for PSE (Philippine Stock Exchange) stocks.

**Facade module** — re-exports public functions from the per-domain service
modules so that existing imports (``from ph_stocks_advisor.data.tools import …``)
continue to work without changes.

New code should import directly from the service modules when possible:
- ``price_service`` — current price snapshots, catalyst detection
- ``dividend_service`` — dividend data, sustainability analysis
- ``movement_service`` — 1-year price movement, candlestick patterns, TV perf
- ``valuation_service`` — fair-value estimation (Graham Number)
- ``controversy_service`` — price anomalies, risk factors, web news
- ``sentiment_service`` — global events, macro-risk, market sentiment
"""

from __future__ import annotations

# Symbol validation (delegates to DragonFi)
from ph_stocks_advisor.data.clients.dragonfi import (  # noqa: F401
    SymbolNotFoundError,
    validate_pse_symbol,
)
from ph_stocks_advisor.data.services.controversy import fetch_controversy_info  # noqa: F401
from ph_stocks_advisor.data.services.dividend import fetch_dividend_info  # noqa: F401
from ph_stocks_advisor.data.services.movement import fetch_price_movement  # noqa: F401

# Domain services (re-exports)
from ph_stocks_advisor.data.services.price import (  # noqa: F401
    detect_price_catalysts as _detect_price_catalysts,
)
from ph_stocks_advisor.data.services.price import (
    fetch_stock_price,
)
from ph_stocks_advisor.data.services.sentiment import fetch_sentiment_info  # noqa: F401
from ph_stocks_advisor.data.services.valuation import fetch_fair_value  # noqa: F401


def validate_symbol(symbol: str) -> str:
    """Validate that *symbol* is a real PSE stock.

    Uses DragonFi (covers all PSE-listed securities) as primary validator.
    Returns the canonical PSE stock code (e.g. ``"AREIT"``).

    Raises:
        SymbolNotFoundError: if the symbol is not found.
    """
    return validate_pse_symbol(symbol)


# Re-export so existing imports keep working.
__all__ = [
    "SymbolNotFoundError",
    "validate_symbol",
    "fetch_stock_price",
    "fetch_dividend_info",
    "fetch_price_movement",
    "fetch_fair_value",
    "fetch_controversy_info",
    "fetch_sentiment_info",
    "_detect_price_catalysts",
]
