from fastapi import APIRouter, HTTPException, status

from app.core.config import settings
from app.core.exceptions import (
    AiUsageLimitExceededError,
    ContentBlockedError,
    FirestoreServiceError,
    OpenAIServiceError,
)
from app.schemas.ai_ask import AiAskRequest, AiAskResponse
from app.services import (
    ai_usage_service,
    content_guard_service,
    openai_service,
    sanitization_service,
)

router = APIRouter()


@router.post("/ai/ask", response_model=AiAskResponse)
async def ask_ai(request: AiAskRequest) -> AiAskResponse:
    try:
        content_guard_service.check_allowed(request.message)
    except ContentBlockedError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc

    sanitized_message = sanitization_service.sanitize_request(request.message, request.context)

    try:
        usage_count, daily_limit, date_key = await ai_usage_service.increment_usage(request.userId)
    except AiUsageLimitExceededError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="AI usage limit exceeded",
        ) from exc
    except FirestoreServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error",
        ) from exc

    try:
        reply = await openai_service.ask_chat(sanitized_message)
    except OpenAIServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI service unavailable",
        ) from exc

    remaining = daily_limit - usage_count

    return AiAskResponse(
        userId=request.userId,
        reply=reply,
        usageCount=usage_count,
        remaining=remaining,
        dateKey=date_key,
        version=settings.VERSION,
    )
