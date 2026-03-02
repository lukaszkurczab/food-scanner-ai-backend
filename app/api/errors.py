"""FastAPI exception handler registration for shared domain errors."""

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.core.exceptions import (
    AiUsageLimitExceededError,
    ContentBlockedError,
    FirestoreServiceError,
    OpenAIServiceError,
)


def register_exception_handlers(app: FastAPI) -> None:
    """Register shared exception handlers for domain/service errors."""

    @app.exception_handler(AiUsageLimitExceededError)
    async def handle_ai_limit_exceeded(
        _request: Request,
        _exc: AiUsageLimitExceededError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={"detail": "AI usage limit exceeded"},
        )

    @app.exception_handler(ContentBlockedError)
    async def handle_content_blocked(
        _request: Request,
        _exc: ContentBlockedError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"detail": "Content is not allowed"},
        )

    @app.exception_handler(OpenAIServiceError)
    async def handle_openai_service_error(
        _request: Request,
        _exc: OpenAIServiceError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": "AI service unavailable"},
        )

    @app.exception_handler(FirestoreServiceError)
    async def handle_firestore_service_error(
        _request: Request,
        _exc: FirestoreServiceError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Database error"},
        )
