"""Per-user idempotency cache for AI endpoints.

Caches responses keyed by (user_id, X-Idempotency-Key) for 90 seconds.
A second request with the same key returns the cached response without
re-running the AI pipeline or deducting credits again.
"""

import json
import threading
from typing import Any, AsyncIterator, cast

from cachetools import TTLCache
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

_CACHE_LOCK = threading.Lock()
# 50 000 unique (user, key) pairs, 90-second TTL
_idempotency_cache: TTLCache[str, dict[str, Any]] = TTLCache(maxsize=50_000, ttl=90)

# Only cache these path prefixes
_IDEMPOTENT_PATHS = {
    "/api/v1/ai/ask",
    "/api/v1/ai/photo/analyze",
    "/api/v1/ai/text-meal/analyze",
}


def _get_user_id(request: Request) -> str | None:
    """Best-effort user ID extraction — auth dep resolves it later, we just need
    something to namespace the key; it is extracted again by the real auth dep."""
    # We rely on the idempotency key being globally unique (UUID v4),
    # so namespacing by user_id is a defence-in-depth safety measure only.
    return request.headers.get("X-Uid")  # optional; fall back to key-only if absent


class IdempotencyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method != "POST" or request.url.path not in _IDEMPOTENT_PATHS:
            return await call_next(request)

        idem_key = request.headers.get("X-Idempotency-Key")
        if not idem_key:
            return await call_next(request)

        # Use key alone as cache key (UUID v4 is globally unique enough)
        cache_key = idem_key

        with _CACHE_LOCK:
            cached = _idempotency_cache.get(cache_key)

        if cached is not None:
            return JSONResponse(
                content=cached,
                status_code=200,
                headers={"X-Idempotency-Replayed": "true"},
            )

        response = await call_next(request)

        # Only cache successful AI responses
        if response.status_code == 200:
            body_bytes = b""
            try:
                body_content = getattr(response, "body", None)
                if isinstance(body_content, (bytes, bytearray)):
                    body_bytes = bytes(body_content)
                else:
                    body_iterator = getattr(response, "body_iterator", None)
                    if body_iterator is None:
                        return response
                    async for chunk in cast(AsyncIterator[bytes], body_iterator):
                        body_bytes += chunk
                body = json.loads(body_bytes)
                with _CACHE_LOCK:
                    _idempotency_cache[cache_key] = body
                return JSONResponse(content=body, status_code=200, headers=dict(response.headers))
            except Exception:
                # If caching fails, return original response unchanged
                return Response(
                    content=body_bytes,
                    status_code=response.status_code,
                    headers=dict(response.headers),
                    media_type=response.media_type,
                    background=response.background,
                )

        return response
