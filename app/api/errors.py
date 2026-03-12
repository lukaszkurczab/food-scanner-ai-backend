"""FastAPI exception handler registration for shared domain errors."""

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.core.exceptions import (
    ContentBlockedError,
    FirestoreServiceError,
    OpenAIServiceError,
)


async def handle_content_blocked(
    _request: Request,
    _exc: Exception,
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_403_FORBIDDEN,
        content={"detail": "Content is not allowed"},
    )


async def handle_openai_service_error(
    _request: Request,
    _exc: Exception,
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": "AI service unavailable"},
    )


async def handle_firestore_service_error(
    _request: Request,
    _exc: Exception,
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Database error"},
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Register shared exception handlers for domain/service errors."""
    app.add_exception_handler(ContentBlockedError, handle_content_blocked)
    app.add_exception_handler(OpenAIServiceError, handle_openai_service_error)
    app.add_exception_handler(FirestoreServiceError, handle_firestore_service_error)
