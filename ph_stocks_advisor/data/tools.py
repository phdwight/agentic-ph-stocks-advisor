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
    fetch_annual_cashflow_trends,
    fetch_annual_income_trends,
    fetch_security_metrics,
    fetch_security_valuation,
    fetch_stock_news,
    fetch_stock_profile,
    validate_pse_symbol,
)
from ph_stocks_advisor.data.candlestick import analyse_candlesticks
from ph_stocks_advisor.data.tradingview import (
    fetch_tradingview_snapshot,
    format_tv_performance_summary,
)
from ph_stocks_advisor.data.tavily_search import (
    search_dividend_news,
    search_stock_controversies,
    search_stock_news,
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
# Price catalyst detection
# ---------------------------------------------------------------------------

def _detect_price_catalysts(profile: dict[str, Any]) -> list[str]:
    """Infer likely price catalysts from DragonFi profile data.

    Cross-references dividend yield, REIT status, and price position
    relative to the 52-week range to identify what may be driving price.
    """
    catalysts: list[str] = []
    if not profile:
        return catalysts

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
    if div_yield > 3.0 and pct_of_range > 65:
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
        if day_change_pct > 0.5 and div_yield > 3.0:
            catalysts.append(
                f"Price rose {day_change_pct:.2f}% from the previous close, "
                f"which may reflect continued demand from dividend-seeking investors."
            )

    # Approaching 52-week high
    if high52 > 0 and price > 0:
        gap_to_high = ((high52 - price) / high52) * 100
        if gap_to_high < 5:
            catalysts.append(
                f"Price is within {gap_to_high:.1f}% of its 52-week high "
                f"(₱{high52}), indicating strong buying pressure."
            )

    return catalysts


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
        catalysts = _detect_price_catalysts(profile)
        return StockPrice(
            symbol=symbol,
            current_price=float(profile.get("price", 0)),
            currency="PHP",
            fifty_two_week_high=float(profile.get("weekHigh52", 0) or 0),
            fifty_two_week_low=float(profile.get("weekLow52", 0) or 0),
            previous_close=float(profile.get("prevDayClosePrice", 0) or 0),
            price_catalysts=catalysts,
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

    Primary: DragonFi (profile + financials)  |  Fallback: yfinance

    Computes:
    - ``dividend_rate`` from yield × price (per-share annual dividend)
    - ``payout_ratio`` from estimated total dividends vs. net income
    - ``is_reit`` flag (Philippine REITs must distribute ≥90% distributable income)
    - ``net_income_trend`` / ``revenue_trend`` / ``free_cash_flow_trend`` — multi-year
    - ``dividend_sustainability_note`` — auto-generated note about sustainability
    """
    symbol = symbol.upper().replace(".PS", "")
    profile = fetch_stock_profile(symbol)

    div_yield_raw = float(profile.get("dividendYield", 0) or 0) if profile else 0.0

    if div_yield_raw > 0 and profile:
        # DragonFi dividend yield is a percentage (e.g. 5.54) → normalise to decimal
        div_yield = div_yield_raw / 100.0 if div_yield_raw > 1 else div_yield_raw

        current_price = float(profile.get("price", 0) or 0)
        shares_outstanding = float(profile.get("sharesOutstanding", 0) or 0)
        is_reit = bool(profile.get("isREIT", False))

        # Compute per-share annual dividend rate from yield × price
        dividend_rate = round(current_price * div_yield, 4) if current_price else 0.0

        # Fetch financial trends
        income_trends = fetch_annual_income_trends(symbol)
        cf_trends = fetch_annual_cashflow_trends(symbol)

        net_income_trend = income_trends.get("net_income", {})
        revenue_trend = income_trends.get("revenue", {})
        fcf_trend = cf_trends.get("fcf", {})

        # Estimate payout ratio: total dividends / net income (latest year)
        payout_ratio = 0.0
        if dividend_rate > 0 and shares_outstanding > 0 and net_income_trend:
            latest_year = max(net_income_trend.keys())
            latest_ni = net_income_trend[latest_year]
            if latest_ni > 0:
                total_dividends = dividend_rate * shares_outstanding
                payout_ratio = round(total_dividends / latest_ni, 4)

        # Build a sustainability note
        sustainability_parts: list[str] = []
        if is_reit:
            sustainability_parts.append(
                "This is a Philippine REIT, legally required to distribute "
                "at least 90% of its distributable income as dividends."
            )
        if len(net_income_trend) >= 3:
            years_sorted = sorted(net_income_trend.keys())
            first_ni = net_income_trend[years_sorted[0]]
            last_ni = net_income_trend[years_sorted[-1]]
            if first_ni > 0 and last_ni > first_ni:
                growth = ((last_ni - first_ni) / first_ni) * 100
                sustainability_parts.append(
                    f"Net income grew ~{growth:.0f}% from {years_sorted[0]} to "
                    f"{years_sorted[-1]} ({first_ni/1e9:.2f}B → {last_ni/1e9:.2f}B PHP), "
                    "supporting the dividend."
                )
            elif last_ni > 0:
                sustainability_parts.append(
                    f"Net income in {years_sorted[-1]}: {last_ni/1e9:.2f}B PHP."
                )
        if payout_ratio > 0:
            sustainability_parts.append(
                f"Estimated payout ratio: {payout_ratio*100:.1f}%."
            )
        if fcf_trend:
            latest_fcf_year = max(fcf_trend.keys())
            latest_fcf = fcf_trend[latest_fcf_year]
            if latest_fcf > 0:
                sustainability_parts.append(
                    f"Free cash flow in {latest_fcf_year}: {latest_fcf/1e9:.2f}B PHP (positive)."
                )

        return DividendInfo(
            symbol=symbol,
            dividend_rate=dividend_rate,
            dividend_yield=div_yield,
            payout_ratio=payout_ratio,
            ex_dividend_date=None,
            five_year_avg_yield=0.0,
            is_reit=is_reit,
            annual_dividend_per_share=dividend_rate,
            net_income_trend=net_income_trend,
            revenue_trend=revenue_trend,
            free_cash_flow_trend=fcf_trend,
            dividend_sustainability_note=" ".join(sustainability_parts),
            recent_dividend_news=search_dividend_news(
                symbol, company_name=str(profile.get("companyName", "")),
            ),
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

    # Fetch profile once — used for catalysts in all branches
    profile = fetch_stock_profile(symbol)
    catalysts = _detect_price_catalysts(profile) if profile else []

    # Tavily web news for movement context
    company_name = profile.get("companyName", "") if profile else ""
    web_news = search_stock_news(symbol, company_name=company_name)

    if not hist.empty:
        closes = hist["Close"].tolist()
        year_start = closes[0]
        year_end = closes[-1]
        year_change_pct = ((year_end - year_start) / year_start) * 100 if year_start else 0
        max_price = max(closes)
        min_price = min(closes)

        returns = hist["Close"].pct_change().dropna()
        volatility = float(returns.std() * 100) if len(returns) > 1 else 0.0

        # Max drawdown: largest peak-to-trough decline
        cummax = hist["Close"].cummax()
        drawdown = (hist["Close"] - cummax) / cummax * 100
        max_drawdown_pct = round(float(drawdown.min()), 2) if len(drawdown) > 0 else 0.0

        # Candlestick pattern analysis (uses full OHLCV)
        candle_summary = analyse_candlesticks(hist)
        candlestick_patterns = candle_summary.to_text()

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
            max_drawdown_pct=max_drawdown_pct,
            trend=trend,
            monthly_prices=monthly_prices,
            price_catalysts=catalysts,
            candlestick_patterns=candlestick_patterns,
            web_news=web_news,
        )

    # Fallback: use DragonFi 52-week range + TradingView performance data
    logger.info("yfinance history unavailable for %s — using DragonFi + TradingView", symbol)

    # TradingView gives accurate multi-period performance & volatility
    tv = fetch_tradingview_snapshot(symbol)
    perf_summary = format_tv_performance_summary(tv)

    if profile:
        high52 = float(profile.get("weekHigh52", 0) or 0)
        low52 = float(profile.get("weekLow52", 0) or 0)
        current = float(profile.get("price", 0) or 0)

        # Use TradingView's 1-year performance if available (more accurate
        # than computing from 52-week low which is misleading)
        tv_year_pct = tv.get("perf_year", 0.0)
        if tv_year_pct:
            change_pct = round(tv_year_pct, 2)
        else:
            change_pct = (
                round(((current - low52) / low52) * 100, 2) if low52 > 0 else 0.0
            )

        if change_pct > 5:
            trend = TrendDirection.UPTREND
        elif change_pct < -5:
            trend = TrendDirection.DOWNTREND
        else:
            trend = TrendDirection.SIDEWAYS

        # Use TradingView monthly volatility if available
        tv_volatility = tv.get("volatility_monthly", 0.0)

        return PriceMovement(
            symbol=symbol,
            year_start_price=round(current / (1 + tv_year_pct / 100), 2) if tv_year_pct else low52,
            year_end_price=current,
            year_change_pct=change_pct,
            max_price=high52,
            min_price=low52,
            volatility=round(tv_volatility, 4),
            max_drawdown_pct=round(((low52 - high52) / high52) * 100, 2) if high52 > 0 else 0.0,
            trend=trend,
            monthly_prices=[],
            price_catalysts=catalysts,
            performance_summary=perf_summary,
            web_news=web_news,
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

    # Web search via Tavily for richer news coverage
    profile = fetch_stock_profile(symbol)
    company_name = str(profile.get("companyName", "")) if profile else ""
    web_general = search_stock_news(symbol, company_name=company_name)
    web_controversy = search_stock_controversies(symbol, company_name=company_name)
    web_news = ""
    if web_general and not web_general.startswith("No "):
        web_news += f"**Recent Web News:**\n{web_general}"
    if web_controversy and not web_controversy.startswith("No "):
        if web_news:
            web_news += "\n\n"
        web_news += f"**Controversy Search:**\n{web_controversy}"
    if not web_news:
        web_news = "No web news available (Tavily API key may not be configured)."

    return ControversyInfo(
        symbol=symbol,
        sudden_spikes=spikes,
        risk_factors=risk_factors,
        recent_news_summary=news_summary,
        web_news=web_news,
    )
