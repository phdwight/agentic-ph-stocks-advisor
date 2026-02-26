"""
Valuation data service — computes fair-value estimates for PSE stocks.

Single Responsibility: only handles valuation data retrieval and
fair-value calculation (Graham Number).
"""

from __future__ import annotations

import math
import logging
from typing import Any

from ph_stocks_advisor.data.clients.dragonfi import (
    fetch_security_valuation,
    fetch_stock_profile,
)
from ph_stocks_advisor.data.models import FairValueEstimate

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _graham_number(eps: float, book_value: float) -> float:
    """Calculate Graham Number: sqrt(22.5 × EPS × BVPS)."""
    if eps > 0 and book_value > 0:
        return round(math.sqrt(22.5 * eps * book_value), 2)
    return 0.0


def _discount_pct(fair_value: float, current_price: float) -> float:
    """Positive = undervalued, negative = overvalued."""
    if fair_value > 0:
        return round(((fair_value - current_price) / fair_value) * 100, 2)
    return 0.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_fair_value(symbol: str) -> FairValueEstimate:
    """Compute a rough fair-value estimate using fundamental ratios.

    Source: DragonFi valuation + metrics. Returns a minimal object when
    data is unavailable.
    """
    symbol = symbol.upper().replace(".PS", "")
    profile = fetch_stock_profile(symbol)
    valuation = fetch_security_valuation(symbol)

    current_price = float(profile.get("price", 0) or 0) if profile else 0.0

    # Extract from DragonFi valuation
    if not valuation:
        valuation = {}
    annual = valuation.get("annualValuation") or {}
    pe_data = annual.get("priceToEarnings") or {}
    pb_data = annual.get("priceToBook") or {}

    pe = float(pe_data.get("Current", 0) or 0)
    pb = float(pb_data.get("Current", 0) or 0)

    # Compute book value per share from PB ratio
    book_value = round(current_price / pb, 2) if pb > 0 else 0.0

    # Compute EPS from PE ratio
    eps = round(current_price / pe, 2) if pe > 0 else 0.0

    # Graham-number estimate
    estimated_fv = _graham_number(eps, book_value)
    if estimated_fv == 0.0 and pe > 0 and current_price > 0:
        estimated_fv = round((current_price / pe) * 15, 2)

    discount = _discount_pct(estimated_fv, current_price)

    if current_price > 0:
        return FairValueEstimate(
            symbol=symbol,
            current_price=current_price,
            book_value=book_value,
            pe_ratio=pe,
            pb_ratio=pb,
            peg_ratio=0.0,
            forward_pe=0.0,
            estimated_fair_value=estimated_fv,
            discount_pct=discount,
        )

    logger.warning("DragonFi returned no valuation data for %s", symbol)
    return FairValueEstimate(symbol=symbol)
