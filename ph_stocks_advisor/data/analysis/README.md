# `ph_stocks_advisor.data.analysis`

## Overview
Pure data-analysis package that transforms OHLCV DataFrames into plain-English candlestick pattern summaries (notable candles, gap events, volume spikes, and multi-day buying/selling pressure) for LLM consumption without performing external I/O.

## Interface
- `[candlestick.py:CandlestickSummary]` - Dataclass holding aggregated candlestick pattern findings (`notable_candles`, `gap_events`, `volume_spikes`, `selling_pressure_periods`, `buying_pressure_periods`) with `.to_text()` formatter.
- `[candlestick.py:CandlestickSummary.to_text]` - Formats detected candlestick findings into a markdown bulleted string or returns `'No notable candlestick patterns detected.'` when all lists are empty.
- `[candlestick.py:analyse_candlesticks]` - Public entry point `(hist: pd.DataFrame) -> CandlestickSummary` that runs all candlestick detectors on an OHLCV DataFrame.

## Invariants
- `[candlestick.py:analyse_candlesticks]` - Requires `hist` to be non-empty and contain columns `{"Open", "High", "Low", "Close"}` (with `DatetimeIndex` elements supporting `.strftime("%Y-%m-%d")`); returns an empty `CandlestickSummary()` otherwise.
- `[candlestick.py:analyse_candlesticks]` - Isolates each detector (`_detect_notable_candles`, `_detect_gaps`, `_detect_volume_spikes`, `_detect_consecutive_pressure`) in its own `try/except Exception` block so individual detector failures do not abort the overall summary.
- `[candlestick.py:_detect_notable_candles]` - Skips rows where `Open == 0`, filters for `abs(body_pct) >= body_threshold_pct` (default `5.0%`), and returns at most `top_n` (default `5`) candles sorted by descending absolute body percentage.
- `[candlestick.py:_detect_gaps]` - Skips rows where previous `Close == 0` and records gap events where `abs(gap_pct) >= gap_threshold_pct` (default `2.0%`).
- `[candlestick.py:_detect_volume_spikes]` - Returns `[]` if `"Volume"` column is absent or sums to `0`; otherwise evaluates rows from index `window` (default `20`, rolling mean `min_periods=5`) onward where `day_vol / avg >= multiplier` (default `3.0`).
- `[candlestick.py:_detect_consecutive_pressure]` - Flushes and resets active streaks when `Open == 0` or candle direction flips (`Close < Open` is bearish, `Close >= Open` is bullish), recording only streaks with `streak_len >= min_streak` (default `3`).

## Side Effects
- `[candlestick.py:analyse_candlesticks]` - Logs warnings via `logging.getLogger(__name__).warning(...)` when any internal detector raises an `Exception`.

## Verification
- Run: `pytest`
