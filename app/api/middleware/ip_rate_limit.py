"""Simple per-IP HTTP rate limiting middleware for public endpoints."""

from __future__ import annotations

from collections import deque
import threading
import time

from cachetools import TTLCache
from fastapi import Request
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

_RATE_LIMIT_MAX_REQUESTS = 300
_RATE_LIMIT_WINDOW_SECONDS = 60.0
_RETRY_AFTER_SECONDS = 60
_CACHE_TTL_SECONDS = 120
_EXEMPT_PATHS = {"/health", "/health/firestore"}

_BUCKET_LOCK = threading.Lock()
_ip_buckets: TTLCache[str, deque[float]] = TTLCache(
    maxsize=50_000,
    ttl=_CACHE_TTL_SECONDS,
)


def _get_client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        first_ip = forwarded_for.split(",")[0].strip()
        if first_ip:
            return first_ip

    client_host = request.client.host if request.client else None
    if client_host:
        return client_host
    return "unknown"


class IpRateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path in _EXEMPT_PATHS:
            return await call_next(request)

        now = time.monotonic()
        client_ip = _get_client_ip(request)
        threshold = now - _RATE_LIMIT_WINDOW_SECONDS

        with _BUCKET_LOCK:
            bucket = _ip_buckets.get(client_ip)
            if bucket is None:
                bucket = deque()
                _ip_buckets[client_ip] = bucket

            while bucket and bucket[0] <= threshold:
                bucket.popleft()

            if len(bucket) >= _RATE_LIMIT_MAX_REQUESTS:
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Too many requests"},
                    headers={"Retry-After": str(_RETRY_AFTER_SECONDS)},
                )

            bucket.append(now)

        return await call_next(request)
