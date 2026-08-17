"""Per-IP sliding-window limits for /auth/login and /auth/register (Redis-backed when configured)."""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import defaultdict, deque
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import settings
from app.core.redis_client import get_async_redis
from app.services.distributed_limits import async_auth_ip_hit

logger = logging.getLogger(__name__)

_auth_mem: dict[tuple[str, str], deque] = defaultdict(lambda: deque())
_auth_mem_lock = threading.Lock()


def _client_ip(request: Request) -> str:
    if settings.trust_forwarded_headers:
        xff = request.headers.get("x-forwarded-for") or request.headers.get("X-Forwarded-For")
        if xff:
            return xff.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _auth_mem_hit(bucket: str, ip: str, limit: int, window: float) -> tuple[bool, int]:
    now = time.time()
    key = (bucket, ip)
    with _auth_mem_lock:
        dq = _auth_mem[key]
        cutoff = now - window
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= limit:
            retry = int(window - (now - dq[0])) + 1 if dq else int(window)
            return False, max(1, retry)
        dq.append(now)
        return True, 0


class AuthIpRateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if request.method != "POST":
            return await call_next(request)

        path = request.url.path.rstrip("/")
        bucket: str | None = None
        limit = 0
        if path.endswith("/auth/login"):
            bucket, limit = "login", settings.auth_login_max_per_minute_per_ip
        elif path.endswith("/auth/register"):
            bucket, limit = "register", settings.auth_register_max_per_minute_per_ip

        if bucket is None:
            return await call_next(request)

        ip = _client_ip(request)
        r = await get_async_redis()
        try:
            if r:
                ok, retry_after = await async_auth_ip_hit(r, bucket, ip, limit, 60.0)
            else:
                ok, retry_after = await asyncio.to_thread(_auth_mem_hit, bucket, ip, limit, 60.0)
        except Exception as exc:
            logger.warning("[AuthRateLimit] check failed (fail-open): %s", exc)
            return await call_next(request)

        if not ok:
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests from this network. Please try again later."},
                headers={"Retry-After": str(max(1, retry_after))},
            )

        return await call_next(request)
