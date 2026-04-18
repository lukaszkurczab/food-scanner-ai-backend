import threading
from collections import deque
from time import monotonic

from cachetools import TTLCache
from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.deps import AuthenticatedUser, get_optional_authenticated_user
from app.schemas.logs import ErrorLogRequest
from app.services import error_logger

router = APIRouter()
RATE_LIMIT_WINDOW_SECONDS = 60.0
RATE_LIMIT_MAX_REQUESTS = 30
_BUCKET_LOCK = threading.Lock()
_request_buckets: TTLCache[str, deque[float]] = TTLCache(
    maxsize=5_000,
    ttl=RATE_LIMIT_WINDOW_SECONDS * 2,
)


def _check_rate_limit(bucket_key: str) -> None:
    now = monotonic()
    with _BUCKET_LOCK:
        bucket = _request_buckets.get(bucket_key)
        if bucket is None:
            bucket = deque[float]()
            _request_buckets[bucket_key] = bucket
        threshold = now - RATE_LIMIT_WINDOW_SECONDS
        while bucket and bucket[0] <= threshold:
            bucket.popleft()
        if len(bucket) >= RATE_LIMIT_MAX_REQUESTS:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many log requests",
            )
        bucket.append(now)


@router.post("/logs/error", status_code=status.HTTP_201_CREATED)
def create_error_log(
    http_request: Request,
    request: ErrorLogRequest,
    current_user: AuthenticatedUser | None = Depends(get_optional_authenticated_user),
) -> dict[str, str]:
    try:
        client_host = (http_request.client.host if http_request.client else "") or "anonymous"
        bucket_key = f"user:{current_user.uid}" if current_user else f"ip:{client_host}"
        _check_rate_limit(bucket_key)
        error_logger.log_error(
            request.message,
            source=request.source,
            stack=request.stack,
            context=request.context,
            userId=current_user.uid if current_user else None,
        )
        return {"detail": "logged"}
    except HTTPException:
        raise
    except Exception as exc:
        error_logger.capture_exception(exc)
        raise HTTPException(status_code=500, detail="Failed to log error") from exc
