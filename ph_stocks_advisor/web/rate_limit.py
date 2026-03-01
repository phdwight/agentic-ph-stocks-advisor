"""
Per-user daily analysis rate limiting.

Single Responsibility: this module only manages the daily counter
for analysis requests per user.  It uses Redis for atomic counting
with automatic expiry at the next UTC midnight.

Dependency Inversion: callers pass in a Redis client and limit value
rather than importing concrete settings directly.
"""

from __future__ import annotations

import calendar
import logging
from datetime import UTC, datetime, timedelta

import redis as redis_lib

logger = logging.getLogger(__name__)

# Redis key prefix for daily analysis counters.
_RATE_LIMIT_PREFIX = "ratelimit:analyse:"


def _seconds_until_utc_midnight() -> int:
    """Return the number of seconds from now until the next 00:00 UTC."""
    now = datetime.now(tz=UTC)
    tomorrow = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return int((tomorrow - now).total_seconds())


def _daily_key(user_id: str) -> str:
    """Build the Redis key for today's counter (UTC date)."""
    today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    return f"{_RATE_LIMIT_PREFIX}{user_id}:{today}"


def check_limit(
    r: redis_lib.Redis,
    user_id: str,
    limit: int,
) -> tuple[bool, int]:
    """Check whether the user may perform another analysis today.

    This is a **read-only** check — it does *not* increment the counter.
    Call :func:`increment` separately after a successful analysis.

    Returns
    -------
    tuple[bool, int]
        ``(allowed, current_count)``.
    """
    key = _daily_key(user_id)
    current = r.get(key)
    current_count = int(current) if current is not None else 0

    if current_count >= limit:
        logger.info(
            "Rate limit reached for %s (%d/%d)", user_id, current_count, limit
        )
        return False, current_count

    return True, current_count


def increment(
    r: redis_lib.Redis,
    user_id: str,
) -> int:
    """Increment the daily analysis counter for *user_id*.

    Call this only after a successful first-time analysis so that
    failed or cached requests are never counted.

    Returns the new count.
    """
    key = _daily_key(user_id)
    new_count = r.incr(key)

    # Set expiry only on the first increment (when the key was just created).
    if new_count == 1:
        ttl = _seconds_until_utc_midnight()
        r.expire(key, ttl)

    return new_count


def check_and_increment(
    r: redis_lib.Redis,
    user_id: str,
    limit: int,
) -> tuple[bool, int]:
    """Check whether the user may perform another analysis today.

    If the current count is below *limit*, the counter is atomically
    incremented and ``(True, new_count)`` is returned.

    If the user has already reached or exceeded *limit*,
    ``(False, current_count)`` is returned and the counter is **not**
    incremented.

    The key automatically expires at the next 00:00 UTC so counters
    reset daily without a cron job.

    .. deprecated::
        Prefer :func:`check_limit` + :func:`increment` so that only
        successful analyses consume quota.

    Parameters
    ----------
    r:
        A Redis client with ``decode_responses=True``.
    user_id:
        A stable identifier for the user (typically their e-mail).
    limit:
        Maximum number of analyses allowed per UTC day.

    Returns
    -------
    tuple[bool, int]
        ``(allowed, count)`` — *allowed* is ``True`` when the request
        may proceed; *count* is the user's total for today **after**
        the increment (or the current total if denied).
    """
    allowed, current_count = check_limit(r, user_id, limit)
    if not allowed:
        return False, current_count

    new_count = increment(r, user_id)
    return True, new_count


def get_remaining(
    r: redis_lib.Redis,
    user_id: str,
    limit: int,
) -> int:
    """Return how many analyses the user has left today."""
    key = _daily_key(user_id)
    current = r.get(key)
    used = int(current) if current is not None else 0
    return max(limit - used, 0)
