"""
Per-user daily analysis rate limiting.

Single Responsibility: this module only manages the daily counter
for analysis requests per user.  It uses Redis for atomic counting
with automatic expiry at the next UTC midnight.

Dependency Inversion: callers pass in a Redis client and limit value
rather than importing concrete settings directly.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import redis as redis_lib

logger = logging.getLogger(__name__)

# Redis key prefix for daily analysis counters.
_RATE_LIMIT_PREFIX = "ratelimit:analyse:"

# Lua script: atomically check the counter and conditionally INCR.
# Returns {1, new_count} when allowed (incremented),
# or     {0, current_count} when the limit is reached (unchanged).
# This eliminates the race between a separate GET and INCR.
_RESERVE_LUA = """
local key   = KEYS[1]
local limit = tonumber(ARGV[1])
local ttl   = tonumber(ARGV[2])

local current = tonumber(redis.call('GET', key) or '0')
if current >= limit then
    return {0, current}
end

local new = redis.call('INCR', key)
if new == 1 then
    redis.call('EXPIRE', key, ttl)
end
return {1, new}
"""


def _seconds_until_utc_midnight() -> int:
    """Return the number of seconds from now until the next 00:00 UTC."""
    now = datetime.now(tz=UTC)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return int((tomorrow - now).total_seconds())


def _daily_key(user_id: str) -> str:
    """Build the Redis key for today's counter (UTC date)."""
    today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    return f"{_RATE_LIMIT_PREFIX}{user_id}:{today}"


# ---------------------------------------------------------------------------
# Atomic reserve / release API  (preferred)
# ---------------------------------------------------------------------------


def reserve(
    r: redis_lib.Redis,
    user_id: str,
    limit: int,
) -> tuple[bool, int]:
    """Atomically check the daily quota and reserve a slot if available.

    Uses a server-side Lua script so the check-then-increment is a
    single atomic Redis operation — no race window between concurrent
    requests from the same user.

    Returns
    -------
    tuple[bool, int]
        ``(allowed, count)`` — *allowed* is ``True`` and *count* is the
        new total after reservation, or ``False`` and the current total
        when the limit has been reached.
    """
    key = _daily_key(user_id)
    ttl = _seconds_until_utc_midnight()

    allowed_int, count = r.eval(_RESERVE_LUA, 1, key, limit, ttl)  # type: ignore[misc]
    allowed = bool(int(allowed_int))  # type: ignore[arg-type]

    if not allowed:
        logger.info("Rate limit reached for %s (%d/%d)", user_id, count, limit)

    return allowed, int(count)  # type: ignore[arg-type]


def release(
    r: redis_lib.Redis,
    user_id: str,
) -> int:
    """Release a previously reserved slot (e.g. after a failed analysis).

    Decrements the counter, clamping at zero so a spurious release
    can never produce a negative count.

    Returns the count after the release.
    """
    key = _daily_key(user_id)
    new_count = r.decr(key)
    if new_count < 0:  # type: ignore[operator]
        r.set(key, "0")
        new_count = 0
    return new_count  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Legacy helpers (kept for backward compatibility)
# ---------------------------------------------------------------------------


def check_limit(
    r: redis_lib.Redis,
    user_id: str,
    limit: int,
) -> tuple[bool, int]:
    """Read-only check — does *not* reserve a slot.

    .. deprecated::
        Use :func:`reserve` instead for race-free limiting.
    """
    key = _daily_key(user_id)
    current = r.get(key)
    current_count = int(current) if current is not None else 0  # type: ignore[arg-type]

    if current_count >= limit:
        logger.info("Rate limit reached for %s (%d/%d)", user_id, current_count, limit)
        return False, current_count

    return True, current_count


def increment(
    r: redis_lib.Redis,
    user_id: str,
) -> int:
    """Increment the daily counter (non-atomic with check_limit).

    .. deprecated::
        Use :func:`reserve` instead for race-free limiting.
    """
    key = _daily_key(user_id)
    new_count = r.incr(key)

    if new_count == 1:
        ttl = _seconds_until_utc_midnight()
        r.expire(key, ttl)

    return new_count  # type: ignore[return-value]


def check_and_increment(
    r: redis_lib.Redis,
    user_id: str,
    limit: int,
) -> tuple[bool, int]:
    """Legacy non-atomic check + increment.

    .. deprecated::
        Use :func:`reserve` instead for race-free limiting.
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
    used = int(current) if current is not None else 0  # type: ignore[arg-type]
    return max(limit - used, 0)
