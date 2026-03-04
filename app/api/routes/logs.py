from collections import deque
from time import monotonic

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.deps import AuthenticatedUser, get_optional_authenticated_user
from app.schemas.logs import ErrorLogRequest
from app.services import error_logger

router = APIRouter()
RATE_LIMIT_WINDOW_SECONDS = 60.0
RATE_LIMIT_MAX_REQUESTS = 30
_request_buckets: dict[str, deque[float]] = {}


def _check_rate_limit(bucket_key: str) -> None:
    now = monotonic()
    bucket = _request_buckets.setdefault(bucket_key, deque())
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
