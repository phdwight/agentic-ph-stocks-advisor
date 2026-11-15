"""
Bangko Sentral ng Pilipinas (BSP) key interest rates service.

Single Responsibility: orchestrates fetching of official BSP policy and money-market
interest rates into the :class:`~ph_stocks_advisor.data.models.BSPKeyRates` model.
"""

from __future__ import annotations

from ph_stocks_advisor.data.clients.bsp import fetch_bsp_key_rates
from ph_stocks_advisor.data.models import BSPKeyRates


def fetch_bsp_rates() -> BSPKeyRates:
    """Fetch current BSP key interest rates and indicators."""
    return fetch_bsp_key_rates()
