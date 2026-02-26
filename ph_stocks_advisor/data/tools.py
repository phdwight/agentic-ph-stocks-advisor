"""
Market data tools for PSE (Philippine Stock Exchange) stocks.

**Data strategy:**
- **DragonFi API** (``api.dragonfi.ph``) is the *primary* source for current
  price snapshots, dividends, fundamentals and valuation ratios.  It covers
  all PSE-listed securities.
- **yfinance** is the *fallback / supplement* used mainly for 1-year daily
  price history (needed by the price-movement and controversy agents).  Not
  every PSE ticker exists on Yahoo Finance.

Each public function follows the Interface Segregation Principle: callers
depend only on the data slice they need.
"""

from __future__ import annotations

import math
import datetime as dt
import logging
from typing import Any

import yfinance as yf

from ph_stocks_advisor.data.dragonfi import (
    SymbolNotFoundError,
    fetch_security_metrics,
    fetch_security_valuation,
    fetch_stock_news,
    fetch_stock_profile,
    validate_pse_symbol,
)
from ph_stocks_advisor.data.models import (
    ControversyInfo,
    DividendInfo,
    FairValueEstimate,
    PriceMovement,
    StockPrice,
    TrendDirection,
)

logger = logging.getLogger(__name__)

# Re-export so existing imports keep working.
__all__ = [
    "SymbolNotFoundError",
    "validate_symbol",
    "fetch_stock_price",
    "fetch_dividend_info",
    "fetch_price_movement",
    "fetch_fair_value",
    "fetch_controversy_info",
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _safe(d: dict[str, Any], key: str, default: Any = 0.0) -> Any:
    val = d.get(key, default)
    return default if val is None else val


def _ticker(symbol: str) -> yf.Ticker:
    """Return a yfinance Ticker (``SYMBOL.PS``)."""
    canon = symbol.upper().replace(".PS", "")
    return yf.Ticker(f"{canon}.PS")


def _yf_history(symbol: str, period: str = "1y"):
    """Best-effort 1-year price history from yfinance.

    Returns a DataFrame (possibly empty) and never raises.
    """
    try:
        t = _ticker(symbol)
        hist = t.history(period=period)
        if hist is not None and not hist.empty:
            return hist
    except Exception as exc:
        logger.debug("yfinance history unavailable for %s: %s", symbol, exc)
    import pandas as pd
    return pd.DataFrame()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_symbol(symbol: str) -> str:
    """Validate that *symbol* is a real PSE stock.

    Uses DragonFi (covers all PSE-listed securities) as primary validator.
    Returns the canonical PSE stock code (e.g. ``"AREIT"``).

    Raises:
        SymbolNotFoundError: if the symbol is not found.
    """
    return validate_pse_symbol(symbol)


# ---------------------------------------------------------------------------
# Public tool functions
# ---------------------------------------------------------------------------

def fetch_stock_price(symbol: str) -> StockPrice:
    """Fetch current price snapshot for a PSE-listed stock.

    Primary: DragonFi  |  Fallback: yfinance
    """
    symbol = symbol.upper().replace(".PS", "")
    profile = fetch_stock_profile(symbol)

    if profile and profile.get("price"):
        return StockPrice(
            symbol=symbol,
            current_price=float(profile.get("price", 0)),
            currency="PHP",
            fifty_two_week_high=float(profile.get("weekHigh52", 0) or 0),
            fifty_two_week_low=float(profile.get("weekLow52", 0) or 0),
            previous_close=float(profile.get("prevDayClosePrice", 0) or 0),
        )

    # Fallback: yfinance
    logger.info("DragonFi profile empty for %s — falling back to yfinance", symbol)
    t = _ticker(symbol)
    info = t.info or {}
    current = _safe(info, "currentPrice") or _safe(info, "regularMarketPrice")
    return StockPrice(
        symbol=symbol,
        current_price=float(current),
        currency=str(_safe(info, "currency", "PHP")),
        fifty_two_week_high=float(_safe(info, "fiftyTwoWeekHigh")),
        fifty_two_week_low=float(_safe(info, "fiftyTwoWeekLow")),
        previous_close=float(_safe(info, "previousClose")),
    )


def fetch_dividend_info(symbol: str) -> DividendInfo:
    """Fetch dividend data for a PSE-listed stock.

    Primary: DragonFi  |  Fallback: yfinance
    """
    symbol = symbol.upper().replace(".PS", "")
    profile = fetch_stock_profile(symbol)
    metrics = fetch_security_metrics(symbol)

    div_yield = float(profile.get("dividendYield", 0) or 0) if profile else 0.0

    # DragonFi dividend yield is already a percentage (e.g. 5.54)
    # Normalise to decimal for consistency with our model (0.0554)
    div_yield_decimal = div_yield / 100.0 if div_yield > 1 else div_yield

    payout_ratio = 0.0

    if div_yield > 0:
        return DividendInfo(
            symbol=symbol,
            dividend_rate=0.0,  # DragonFi doesn't expose per-share rate directly
            dividend_yield=div_yield_decimal,
            payout_ratio=payout_ratio,
            ex_dividend_date=None,
            five_year_avg_yield=0.0,
        )

    # Fallback: yfinance
    logger.info("DragonFi dividend data empty for %s — falling back to yfinance", symbol)
    t = _ticker(symbol)
    info = t.info or {}
    ex_date_raw = info.get("exDividendDate")
    ex_date = None
    if ex_date_raw:
        try:
            ex_date = dt.datetime.fromtimestamp(ex_date_raw).strftime("%Y-%m-%d")
        except Exception:
            ex_date = str(ex_date_raw)

    return DividendInfo(
        symbol=symbol,
        dividend_rate=float(_safe(info, "dividendRate")),
        dividend_yield=float(_safe(info, "dividendYield")),
        payout_ratio=float(_safe(info, "payoutRatio")),
        ex_dividend_date=ex_date,
        five_year_avg_yield=float(_safe(info, "fiveYearAvgDividendYield")),
    )


def fetch_price_movement(symbol: str) -> PriceMovement:
    """Fetch 1-year price history and compute movement metrics.

    Uses yfinance for daily history (DragonFi doesn't expose a historical
    price series).  If yfinance has no data, returns a minimal result using
    the 52-week range from DragonFi.
    """
    symbol = symbol.upper().replace(".PS", "")
    hist = _yf_history(symbol)

    if not hist.empty:
        closes = hist["Close"].tolist()
        year_start = closes[0]
        year_end = closes[-1]
        year_change_pct = ((year_end - year_start) / year_start) * 100 if year_start else 0
        max_price = max(closes)
        min_price = min(closes)

        returns = hist["Close"].pct_change().dropna()
        volatility = float(returns.std() * 100) if len(returns) > 1 else 0.0

        monthly = hist["Close"].resample("ME").mean()
        monthly_prices = [round(float(p), 2) for p in monthly.tolist()]

        if year_change_pct > 5:
            trend = TrendDirection.UPTREND
        elif year_change_pct < -5:
            trend = TrendDirection.DOWNTREND
        else:
            trend = TrendDirection.SIDEWAYS

        return PriceMovement(
            symbol=symbol,
            year_start_price=round(year_start, 2),
            year_end_price=round(year_end, 2),
            year_change_pct=round(year_change_pct, 2),
            max_price=round(max_price, 2),
            min_price=round(min_price, 2),
            volatility=round(volatility, 4),
            trend=trend,
            monthly_prices=monthly_prices,
        )

    # Fallback: use DragonFi 52-week range for a rough estimate
    logger.info("yfinance history unavailable for %s — using DragonFi 52-week range", symbol)
    profile = fetch_stock_profile(symbol)
    if profile:
        high52 = float(profile.get("weekHigh52", 0) or 0)
        low52 = float(profile.get("weekLow52", 0) or 0)
        current = float(profile.get("price", 0) or 0)
        change_pct = (
            round(((current - low52) / low52) * 100, 2) if low52 > 0 else 0.0
        )
        if change_pct > 5:
            trend = TrendDirection.UPTREND
        elif change_pct < -5:
            trend = TrendDirection.DOWNTREND
        else:
            trend = TrendDirection.SIDEWAYS

        return PriceMovement(
            symbol=symbol,
            year_start_price=low52,
            year_end_price=current,
            year_change_pct=change_pct,
            max_price=high52,
            min_price=low52,
            volatility=0.0,
            trend=trend,
            monthly_prices=[],
        )

    return PriceMovement(symbol=symbol)


def fetch_fair_value(symbol: str) -> FairValueEstimate:
    """Compute a rough fair-value estimate using fundamental ratios.

    Primary: DragonFi valuation + metrics  |  Fallback: yfinance
    """
    symbol = symbol.upper().replace(".PS", "")
    profile = fetch_stock_profile(symbol)
    valuation = fetch_security_valuation(symbol)

    current_price = float(profile.get("price", 0) or 0) if profile else 0.0

    # Extract from DragonFi valuation
    annual = valuation.get("annualValuation", {})
    pe_data = annual.get("priceToEarnings", {})
    pb_data = annual.get("priceToBook", {})

    pe = float(pe_data.get("Current", 0) or 0)
    pb = float(pb_data.get("Current", 0) or 0)

    # Compute book value per share from PB ratio
    book_value = round(current_price / pb, 2) if pb > 0 else 0.0

    # Compute EPS from PE ratio
    eps = round(current_price / pe, 2) if pe > 0 else 0.0

    # Graham-number estimate: sqrt(22.5 * EPS * BVPS)
    if eps > 0 and book_value > 0:
        estimated_fv = round(math.sqrt(22.5 * eps * book_value), 2)
    elif pe > 0 and current_price > 0:
        estimated_fv = round((current_price / pe) * 15, 2)
    else:
        estimated_fv = 0.0

    discount_pct = (
        round(((estimated_fv - current_price) / estimated_fv) * 100, 2)
        if estimated_fv > 0
        else 0.0
    )

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
            discount_pct=discount_pct,
        )

    # Fallback: yfinance
    logger.info("DragonFi valuation empty for %s — falling back to yfinance", symbol)
    t = _ticker(symbol)
    info = t.info or {}
    current_price = float(
        _safe(info, "currentPrice") or _safe(info, "regularMarketPrice")
    )
    book_value = float(_safe(info, "bookValue"))
    pe = float(_safe(info, "trailingPE"))
    pb = float(_safe(info, "priceToBook"))
    peg = float(_safe(info, "pegRatio"))
    forward_pe = float(_safe(info, "forwardPE"))
    eps = float(_safe(info, "trailingEps"))
    if eps > 0 and book_value > 0:
        estimated_fv = round(math.sqrt(22.5 * eps * book_value), 2)
    elif pe > 0 and current_price > 0:
        estimated_fv = round((current_price / pe) * 15, 2)
    else:
        estimated_fv = 0.0
    discount_pct = (
        round(((estimated_fv - current_price) / estimated_fv) * 100, 2)
        if estimated_fv > 0
        else 0.0
    )
    return FairValueEstimate(
        symbol=symbol,
        current_price=current_price,
        book_value=book_value,
        pe_ratio=pe,
        pb_ratio=pb,
        peg_ratio=peg,
        forward_pe=forward_pe,
        estimated_fair_value=estimated_fv,
        discount_pct=discount_pct,
    )


def fetch_controversy_info(symbol: str) -> ControversyInfo:
    """Detect potential price anomalies and gather recent news.

    Uses yfinance for daily history (spike detection) and DragonFi
    for recent news headlines.
    """
    symbol = symbol.upper().replace(".PS", "")
    hist = _yf_history(symbol)
    spikes: list[str] = []
    risk_factors: list[str] = []

    if not hist.empty:
        returns = hist["Close"].pct_change().dropna()
        std_ret = returns.std()

        for date, ret in returns.items():
            if abs(ret) > 3 * std_ret and abs(ret) > 0.05:
                direction = "spike up" if ret > 0 else "spike down"
                spikes.append(
                    f"{date.strftime('%Y-%m-%d')}: {direction} of {ret*100:.1f}%"
                )

        if std_ret > 0.03:
            risk_factors.append("High daily volatility (std > 3%)")

        avg_price = hist["Close"].mean()
        last_price = hist["Close"].iloc[-1]
        if last_price > avg_price * 1.3:
            risk_factors.append(
                "Current price is >30% above 52-week average — potential overvaluation"
            )
        elif last_price < avg_price * 0.7:
            risk_factors.append(
                "Current price is >30% below 52-week average — potential distress"
            )

    # Fetch recent news from DragonFi
    news_items = fetch_stock_news(symbol, page_size=5)
    if news_items:
        headlines = []
        for item in news_items:
            title = item.get("title") or item.get("headline", "")
            source = item.get("source", "")
            if title:
                headlines.append(f"[{source}] {title}" if source else title)
        news_summary = "\n".join(headlines) if headlines else "No recent news found."
    else:
        news_summary = "No recent news available from DragonFi."

    return ControversyInfo(
        symbol=symbol,
        sudden_spikes=spikes,
        risk_factors=risk_factors,
        recent_news_summary=news_summary,
    )
