"""
Candlestick pattern analysis for Philippine Stock Exchange equities.

Analyses OHLCV (Open, High, Low, Close, Volume) data from yfinance to detect
notable chart patterns and events that a human would spot on a candlestick
chart — large bearish/bullish candles, gap-downs/ups, volume spikes, and
multi-day selling or buying pressure.

Each detector returns plain-English descriptions suitable for LLM consumption.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class CandlestickSummary:
    """Aggregated candlestick pattern findings."""

    notable_candles: list[str] = field(default_factory=list)
    gap_events: list[str] = field(default_factory=list)
    volume_spikes: list[str] = field(default_factory=list)
    selling_pressure_periods: list[str] = field(default_factory=list)
    buying_pressure_periods: list[str] = field(default_factory=list)

    def to_text(self) -> str:
        """Format findings into an LLM-friendly summary string."""
        sections: list[str] = []
        if self.notable_candles:
            sections.append(
                "**Notable Candles:**\n" + "\n".join(f"  • {c}" for c in self.notable_candles)
            )
        if self.gap_events:
            sections.append(
                "**Gap Events:**\n" + "\n".join(f"  • {g}" for g in self.gap_events)
            )
        if self.volume_spikes:
            sections.append(
                "**Volume Spikes:**\n" + "\n".join(f"  • {v}" for v in self.volume_spikes)
            )
        if self.selling_pressure_periods:
            sections.append(
                "**Selling Pressure:**\n"
                + "\n".join(f"  • {s}" for s in self.selling_pressure_periods)
            )
        if self.buying_pressure_periods:
            sections.append(
                "**Buying Pressure:**\n"
                + "\n".join(f"  • {b}" for b in self.buying_pressure_periods)
            )
        return "\n".join(sections) if sections else "No notable candlestick patterns detected."


# ---------------------------------------------------------------------------
# Individual detectors
# ---------------------------------------------------------------------------

def _detect_notable_candles(
    df: pd.DataFrame, *, body_threshold_pct: float = 5.0, top_n: int = 5,
) -> list[str]:
    """Find large bearish or bullish candles (body > threshold %).

    Returns descriptions sorted by absolute body size (largest first).
    """
    results: list[str] = []
    opens = df["Open"].values
    closes = df["Close"].values
    highs = df["High"].values
    lows = df["Low"].values
    dates = df.index

    for i in range(len(df)):
        o, c = float(opens[i]), float(closes[i])
        if o == 0:
            continue
        body_pct = ((c - o) / o) * 100
        if abs(body_pct) >= body_threshold_pct:
            date_str = dates[i].strftime("%Y-%m-%d")
            direction = "bullish (green)" if body_pct > 0 else "bearish (red)"
            h, l = float(highs[i]), float(lows[i])
            results.append(
                (abs(body_pct), f"{date_str}: Large {direction} candle — "
                 f"O:{o:.2f} H:{h:.2f} L:{l:.2f} C:{c:.2f} ({body_pct:+.1f}%)")
            )

    results.sort(key=lambda x: x[0], reverse=True)
    return [r[1] for r in results[:top_n]]


def _detect_gaps(df: pd.DataFrame, *, gap_threshold_pct: float = 2.0) -> list[str]:
    """Detect gap-downs and gap-ups (today's open vs yesterday's close)."""
    results: list[str] = []
    closes = df["Close"].values
    opens = df["Open"].values
    dates = df.index

    for i in range(1, len(df)):
        prev_close = float(closes[i - 1])
        if prev_close == 0:
            continue
        today_open = float(opens[i])
        gap_pct = ((today_open - prev_close) / prev_close) * 100
        if abs(gap_pct) >= gap_threshold_pct:
            date_str = dates[i].strftime("%Y-%m-%d")
            direction = "gap-UP" if gap_pct > 0 else "gap-DOWN"
            results.append(
                f"{date_str}: {direction} of {gap_pct:+.1f}% "
                f"(prev close {prev_close:.2f} → open {today_open:.2f})"
            )

    return results


def _detect_volume_spikes(
    df: pd.DataFrame, *, multiplier: float = 3.0, window: int = 20,
) -> list[str]:
    """Find days where volume exceeds the rolling average by *multiplier* x."""
    if "Volume" not in df.columns or df["Volume"].sum() == 0:
        return []

    vol = df["Volume"].astype(float)
    rolling_avg = vol.rolling(window=window, min_periods=5).mean()
    results: list[str] = []

    for i in range(window, len(df)):
        avg = float(rolling_avg.iloc[i]) if not np.isnan(rolling_avg.iloc[i]) else 0
        if avg == 0:
            continue
        day_vol = float(vol.iloc[i])
        ratio = day_vol / avg
        if ratio >= multiplier:
            date_str = df.index[i].strftime("%Y-%m-%d")
            close_chg = ""
            if i > 0:
                prev_c = float(df["Close"].iloc[i - 1])
                cur_c = float(df["Close"].iloc[i])
                if prev_c > 0:
                    pct = ((cur_c - prev_c) / prev_c) * 100
                    close_chg = f", price {pct:+.1f}%"
            results.append(
                f"{date_str}: Volume spike {ratio:.1f}x average "
                f"({day_vol:,.0f} vs avg {avg:,.0f}{close_chg})"
            )

    return results


def _detect_consecutive_pressure(
    df: pd.DataFrame, *, min_streak: int = 3,
) -> tuple[list[str], list[str]]:
    """Detect streaks of consecutive bearish or bullish candles.

    Returns (selling_pressure, buying_pressure) lists.
    """
    selling: list[str] = []
    buying: list[str] = []
    opens = df["Open"].values
    closes = df["Close"].values
    dates = df.index

    streak_type: str | None = None  # "bear" or "bull"
    streak_start = 0
    streak_len = 0
    cumulative_pct = 0.0

    def _flush() -> None:
        nonlocal streak_type, streak_start, streak_len, cumulative_pct
        if streak_len >= min_streak:
            start_date = dates[streak_start].strftime("%Y-%m-%d")
            end_date = dates[streak_start + streak_len - 1].strftime("%Y-%m-%d")
            desc = (
                f"{start_date} to {end_date}: {streak_len} consecutive "
                f"{'bearish' if streak_type == 'bear' else 'bullish'} candles "
                f"(cumulative {cumulative_pct:+.1f}%)"
            )
            if streak_type == "bear":
                selling.append(desc)
            else:
                buying.append(desc)
        streak_type = None
        streak_len = 0
        cumulative_pct = 0.0

    for i in range(len(df)):
        o, c = float(opens[i]), float(closes[i])
        if o == 0:
            _flush()
            continue
        day_pct = ((c - o) / o) * 100
        current = "bear" if c < o else "bull"

        if current == streak_type:
            streak_len += 1
            cumulative_pct += day_pct
        else:
            _flush()
            streak_type = current
            streak_start = i
            streak_len = 1
            cumulative_pct = day_pct

    _flush()  # final streak
    return selling, buying


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyse_candlesticks(hist: pd.DataFrame) -> CandlestickSummary:
    """Run all candlestick detectors on a yfinance-style OHLCV DataFrame.

    Parameters
    ----------
    hist:
        DataFrame with at least ``Open``, ``High``, ``Low``, ``Close``
        columns and a DatetimeIndex.  ``Volume`` is optional but
        enhances the analysis.

    Returns
    -------
    CandlestickSummary with plain-English findings.
    """
    required = {"Open", "High", "Low", "Close"}
    if hist.empty or not required.issubset(hist.columns):
        return CandlestickSummary()

    summary = CandlestickSummary()

    try:
        summary.notable_candles = _detect_notable_candles(hist)
    except Exception as exc:
        logger.warning("Notable candle detection failed: %s", exc)

    try:
        summary.gap_events = _detect_gaps(hist)
    except Exception as exc:
        logger.warning("Gap detection failed: %s", exc)

    try:
        summary.volume_spikes = _detect_volume_spikes(hist)
    except Exception as exc:
        logger.warning("Volume spike detection failed: %s", exc)

    try:
        selling, buying = _detect_consecutive_pressure(hist)
        summary.selling_pressure_periods = selling
        summary.buying_pressure_periods = buying
    except Exception as exc:
        logger.warning("Pressure detection failed: %s", exc)

    return summary
