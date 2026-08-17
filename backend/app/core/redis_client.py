"""Shared Redis clients for async (request path) and sync (legacy / tight loops)."""
from __future__ import annotations

import logging

import redis.asyncio as aioredis
from redis import Redis as SyncRedis

from app.core.config import settings

logger = logging.getLogger(__name__)

_async_client: aioredis.Redis | None = None
_sync_client: SyncRedis | None = None


def redis_enabled() -> bool:
    return bool(settings.redis_url)


async def get_async_redis() -> aioredis.Redis | None:
    global _async_client
    if not settings.redis_url:
        return None
    if _async_client is None:
        _async_client = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
        logger.info("Redis async client initialised for rate limiting / concurrency.")
    return _async_client


def get_sync_redis() -> SyncRedis | None:
    global _sync_client
    if not settings.redis_url:
        return None
    if _sync_client is None:
        _sync_client = SyncRedis.from_url(settings.redis_url, decode_responses=True)
    return _sync_client


async def close_async_redis() -> None:
    global _async_client
    if _async_client is not None:
        await _async_client.aclose()
        _async_client = None


def close_sync_redis() -> None:
    global _sync_client
    if _sync_client is not None:
        _sync_client.close()
        _sync_client = None
