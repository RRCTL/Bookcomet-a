"""
Limit concurrent long-running handlers that hold a SQLAlchemy session during
OCR / LLM work. Prevents pool exhaustion under many parallel tenants.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from app.core.config import settings

logger = logging.getLogger(__name__)

_heavy_sem: asyncio.Semaphore | None = None


def _heavy_work_semaphore() -> asyncio.Semaphore:
    global _heavy_sem
    if _heavy_sem is None:
        configured = max(1, settings.db_heavy_work_concurrency)
        # Runtime evidence (QueuePool timeout in get_current_user): long OCR/LLM cores can
        # hold a checked-out session for minutes while DB_HEAVY_WORK_CONCURRENCY still
        # allows parallel jobs. Those jobs must stay within pool_size so overflow slots stay
        # available for short-lived API dependency sessions during bursts.
        pool_cap = max(1, settings.db_pool_size)
        limit = min(configured, pool_cap)
        if limit < configured:
            logger.warning(
                "DB_HEAVY_WORK_CONCURRENCY (%s) exceeds DB_POOL_SIZE (%s); capping OCR/LLM "
                "slots to pool_size so authenticated API routes do not starve (QueuePool).",
                configured,
                pool_cap,
            )
        _heavy_sem = asyncio.Semaphore(limit)
    return _heavy_sem


@asynccontextmanager
async def long_running_db_work_slot() -> AsyncIterator[None]:
    sem = _heavy_work_semaphore()
    await sem.acquire()
    try:
        yield
    finally:
        sem.release()
