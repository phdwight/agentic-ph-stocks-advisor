"""
PSE trading-session calendar and cache-freshness cutoffs.

The PSE trades **09:00–15:00 PHT (UTC+8, no DST), Mon–Fri**. A cached report
is considered *fresh* if it was generated after the most recent trading-day
15:00 close; otherwise a new analysis is warranted. New analyses, however,
run only **outside** market hours (see ``is_market_open``).

Holidays are not handled yet — add dates to ``NON_TRADING_DATES`` to close
specific days (weekends are always closed).

All functions accept an optional tz-aware ``now`` so behaviour is
deterministic in tests. Cutoff functions return tz-aware **UTC** datetimes so
they compare directly against stored ``created_at`` values.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta, timezone

PHT = timezone(timedelta(hours=8))

MARKET_OPEN_HOUR = 9  # 09:00 PHT
MARKET_CLOSE_HOUR = 15  # 15:00 PHT (3:00 PM)

# Holiday hook — PSE non-trading dates. Empty for now (weekends-only);
# populate per-year from the PSE trading calendar to close specific days.
NON_TRADING_DATES: set[date] = set()


def _now_pht(now: datetime | None = None) -> datetime:
    """Return ``now`` (or the current time) as a PHT-aware datetime."""
    if now is None:
        return datetime.now(tz=PHT)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(PHT)


def is_trading_day(d: date) -> bool:
    """True if *d* is a PSE trading day (Mon–Fri and not a holiday)."""
    return d.weekday() < 5 and d not in NON_TRADING_DATES


def is_market_open(now: datetime | None = None) -> bool:
    """True during a live session: a trading day and 09:00 <= t < 15:00 PHT."""
    t = _now_pht(now)
    return is_trading_day(t.date()) and MARKET_OPEN_HOUR <= t.hour < MARKET_CLOSE_HOUR


def _close_utc(d: date) -> datetime:
    """The 15:00 PHT close of date *d*, as a tz-aware UTC datetime."""
    return datetime.combine(d, time(hour=MARKET_CLOSE_HOUR), tzinfo=PHT).astimezone(UTC)


def last_trading_close(now: datetime | None = None) -> datetime:
    """Most recent trading-day 15:00 PHT close at or before *now* (UTC).

    Reports created on/after this instant are fresh. Examples (PHT):
    Mon 08:00 → previous Fri 15:00; Mon 16:00 → Mon 15:00; Sat → Fri 15:00.
    """
    t = _now_pht(now)
    d = t.date()
    if is_trading_day(d) and t.hour >= MARKET_CLOSE_HOUR:
        return _close_utc(d)
    d -= timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return _close_utc(d)


def next_trading_close(now: datetime | None = None) -> datetime:
    """Next trading-day 15:00 PHT close strictly after *now* (UTC)."""
    t = _now_pht(now)
    d = t.date()
    if is_trading_day(d) and t.hour < MARKET_CLOSE_HOUR:
        return _close_utc(d)
    d += timedelta(days=1)
    while not is_trading_day(d):
        d += timedelta(days=1)
    return _close_utc(d)


def next_close_label(now: datetime | None = None) -> str:
    """Human label for when the next fresh run becomes available.

    e.g. ``"after today's 3:00 PM PHT close"``, ``"after tomorrow's ..."``,
    or ``"after Monday's 3:00 PM PHT close"``.
    """
    t = _now_pht(now)
    nc_pht = next_trading_close(now).astimezone(PHT)
    if nc_pht.date() == t.date():
        return "after today's 3:00 PM PHT close"
    if nc_pht.date() == t.date() + timedelta(days=1):
        return "after tomorrow's 3:00 PM PHT close"
    return f"after {nc_pht.strftime('%A')}'s 3:00 PM PHT close"
