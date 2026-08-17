"""
Redis-backed sliding-window rate limits and per-company OCR concurrency.

Used when REDIS_URL is set so limits are shared across API instances.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

from app.core.config import settings

if TYPE_CHECKING:
    import redis.asyncio as aioredis
    from redis import Redis as SyncRedis

logger = logging.getLogger(__name__)

_PREFIX = lambda: settings.redis_key_prefix

# Sliding window: ZSET scores = event time (unix float string for consistency)
_LUA_SLIDE_ALLOW = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]
redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local n = redis.call('ZCARD', key)
if n >= limit then
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    local retry = window
    if oldest[2] then
        retry = math.ceil(window - (now - tonumber(oldest[2])))
        if retry < 1 then retry = 1 end
    end
    return {0, retry}
end
redis.call('ZADD', key, now, member)
redis.call('EXPIRE', key, math.ceil(window) + 10)
return {1, 0}
"""

# Per-company concurrent OCR / upload pipeline slots
_LUA_CONCURRENCY_ACQUIRE = """
local key = KEYS[1]
local maxc = tonumber(ARGV[1])
local ttl = tonumber(ARGV[2])
local n = redis.call('INCR', key)
if n == 1 then
    redis.call('EXPIRE', key, ttl)
end
if n > maxc then
    redis.call('DECR', key)
    return 0
end
return 1
"""

_LUA_CONCURRENCY_RELEASE = """
local key = KEYS[1]
local n = redis.call('DECR', key)
if n < 0 then
    redis.call('SET', key, 0)
end
return 1
"""


def _slide_key(kind: str, *parts: str) -> str:
    safe = ":".join(p.replace(":", "_") for p in parts if p)
    return f"{_PREFIX()}:{kind}:{safe}"


async def async_sliding_window_hit(
    r: "aioredis.Redis",
    key: str,
    *,
    limit: int,
    window_seconds: float,
) -> tuple[bool, int]:
    """Returns (allowed, retry_after_seconds)."""
    if limit <= 0:
        return True, 0
    member = f"{time.time():.6f}:{uuid.uuid4().hex}"
    res: Any = await r.eval(
        _LUA_SLIDE_ALLOW,
        1,
        key,
        str(time.time()),
        str(window_seconds),
        str(limit),
        member,
    )
    if not isinstance(res, (list, tuple)):
        return bool(res), 0
    ok = bool(res[0])
    retry = int(res[1]) if len(res) > 1 else 0
    return ok, retry


def sync_sliding_window_hit(
    r: "SyncRedis",
    key: str,
    *,
    limit: int,
    window_seconds: float,
) -> tuple[bool, int]:
    if limit <= 0:
        return True, 0
    member = f"{time.time():.6f}:{uuid.uuid4().hex}"
    res: Any = r.eval(
        _LUA_SLIDE_ALLOW,
        1,
        key,
        str(time.time()),
        str(window_seconds),
        str(limit),
        member,
    )
    if not isinstance(res, (list, tuple)):
        return bool(res), 0
    ok = bool(res[0])
    retry = int(res[1]) if len(res) > 1 else 0
    return ok, retry


async def async_auth_ip_hit(r: "aioredis.Redis", bucket: str, ip: str, limit: int, window: float = 60.0) -> tuple[bool, int]:
    key = _slide_key("auth", bucket, ip)
    return await async_sliding_window_hit(r, key, limit=limit, window_seconds=window)


def sync_auth_ip_hit(r: "SyncRedis", bucket: str, ip: str, limit: int, window: float = 60.0) -> tuple[bool, int]:
    key = _slide_key("auth", bucket, ip)
    return sync_sliding_window_hit(r, key, limit=limit, window_seconds=window)


async def async_chat_rate_hit(
    r: "aioredis.Redis",
    company_id: str,
    *,
    per_minute_limit: int,
    per_day_limit: int,
    ocr_mode: bool,
) -> tuple[bool, str]:
    """
    Matches abuse_guard sliding windows: 60s for per-minute, 86400 for day.
    ocr_mode selects the higher per-minute ceiling (caller passes limits).
    """
    now = time.time()
    min_key = _slide_key("chat", "min", company_id, "ocr" if ocr_mode else "std")
    member_m = f"{now:.6f}:{uuid.uuid4().hex}"
    res_m = await r.eval(
        _LUA_SLIDE_ALLOW,
        1,
        min_key,
        str(now),
        "60",
        str(per_minute_limit),
        member_m,
    )
    if not res_m[0]:
        wait = int(res_m[1]) if len(res_m) > 1 else 60
        return False, f"Too many messages. Please wait {wait} seconds before sending again."

    day_key = _slide_key("chat", "day", company_id)
    member_d = f"{now:.6f}:{uuid.uuid4().hex}"
    res_d = await r.eval(
        _LUA_SLIDE_ALLOW,
        1,
        day_key,
        str(now),
        "86400",
        str(per_day_limit),
        member_d,
    )
    if not res_d[0]:
        return False, f"Daily AI chat limit ({per_day_limit} messages) reached. Resets after 24h."

    return True, ""


def sync_chat_rate_hit(
    r: "SyncRedis",
    company_id: str,
    *,
    per_minute_limit: int,
    per_day_limit: int,
    ocr_mode: bool,
) -> tuple[bool, str]:
    now = time.time()
    min_key = _slide_key("chat", "min", company_id, "ocr" if ocr_mode else "std")
    member_m = f"{now:.6f}:{uuid.uuid4().hex}"
    res_m = r.eval(
        _LUA_SLIDE_ALLOW,
        1,
        min_key,
        str(now),
        "60",
        str(per_minute_limit),
        member_m,
    )
    if not res_m[0]:
        wait = int(res_m[1]) if len(res_m) > 1 else 60
        return False, f"Too many messages. Please wait {wait} seconds before sending again."

    day_key = _slide_key("chat", "day", company_id)
    member_d = f"{now:.6f}:{uuid.uuid4().hex}"
    res_d = r.eval(
        _LUA_SLIDE_ALLOW,
        1,
        day_key,
        str(now),
        "86400",
        str(per_day_limit),
        member_d,
    )
    if not res_d[0]:
        return False, f"Daily AI chat limit ({per_day_limit} messages) reached. Resets after 24h."

    return True, ""


async def async_generation_rate_hit(r: "aioredis.Redis", company_id: str, per_hour_limit: int) -> tuple[bool, str]:
    now = time.time()
    key = _slide_key("gen", "hour", company_id)
    member = f"{now:.6f}:{uuid.uuid4().hex}"
    res = await r.eval(
        _LUA_SLIDE_ALLOW,
        1,
        key,
        str(now),
        "3600",
        str(per_hour_limit),
        member,
    )
    if not res[0]:
        wait_min = max(1, int(int(res[1]) / 60) + 1) if len(res) > 1 else 60
        return False, f"Generation limit ({per_hour_limit}/hour) reached. Try again in ~{wait_min} min."
    return True, ""


def sync_generation_rate_hit(r: "SyncRedis", company_id: str, per_hour_limit: int) -> tuple[bool, str]:
    now = time.time()
    key = _slide_key("gen", "hour", company_id)
    member = f"{now:.6f}:{uuid.uuid4().hex}"
    res = r.eval(
        _LUA_SLIDE_ALLOW,
        1,
        key,
        str(now),
        "3600",
        str(per_hour_limit),
        member,
    )
    if not res[0]:
        wait_min = max(1, int(int(res[1]) / 60) + 1) if len(res) > 1 else 60
        return False, f"Generation limit ({per_hour_limit}/hour) reached. Try again in ~{wait_min} min."
    return True, ""


async def async_ocr_concurrency_acquire(r: "aioredis.Redis", company_id: str, max_concurrent: int, ttl_sec: int = 7200) -> bool:
    key = _slide_key("ocr", "inflight", company_id)
    res = await r.eval(_LUA_CONCURRENCY_ACQUIRE, 1, key, str(max_concurrent), str(ttl_sec))
    return bool(res)


async def async_ocr_concurrency_release(r: "aioredis.Redis", company_id: str) -> None:
    key = _slide_key("ocr", "inflight", company_id)
    try:
        await r.eval(_LUA_CONCURRENCY_RELEASE, 1, key)
    except Exception as exc:
        logger.warning("[Redis] ocr concurrency release failed: %s", exc)


def sync_ocr_concurrency_acquire(r: "SyncRedis", company_id: str, max_concurrent: int, ttl_sec: int = 7200) -> bool:
    key = _slide_key("ocr", "inflight", company_id)
    res = r.eval(_LUA_CONCURRENCY_ACQUIRE, 1, key, str(max_concurrent), str(ttl_sec))
    return bool(res)


def sync_ocr_concurrency_release(r: "SyncRedis", company_id: str) -> None:
    key = _slide_key("ocr", "inflight", company_id)
    try:
        r.eval(_LUA_CONCURRENCY_RELEASE, 1, key)
    except Exception as exc:
        logger.warning("[Redis] ocr concurrency release failed: %s", exc)
