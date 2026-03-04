"""Shared HTTP exception helpers for API routes."""

from typing import NoReturn

from fastapi import HTTPException, status


def raise_http_exception(
    *,
    status_code: int,
    detail: str,
    cause: Exception | None = None,
) -> NoReturn:
    if cause is None:
        raise HTTPException(status_code=status_code, detail=detail)
    raise HTTPException(status_code=status_code, detail=detail) from cause


def raise_bad_request(cause: Exception) -> NoReturn:
    raise_http_exception(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=str(cause),
        cause=cause,
    )


def raise_forbidden(cause: Exception, detail: str) -> NoReturn:
    raise_http_exception(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=detail,
        cause=cause,
    )


def raise_too_many_requests(cause: Exception, detail: str) -> NoReturn:
    raise_http_exception(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=detail,
        cause=cause,
    )


def raise_service_unavailable(cause: Exception, detail: str) -> NoReturn:
    raise_http_exception(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=detail,
        cause=cause,
    )


def raise_database_error(cause: Exception) -> NoReturn:
    raise_http_exception(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Database error",
        cause=cause,
    )
