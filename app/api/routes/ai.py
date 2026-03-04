import logging
from time import perf_counter
from typing import TypedDict

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse

from app.api.deps import (
    AuthenticatedUser,
    get_required_authenticated_user,
)
from app.api.http_errors import (
    raise_database_error,
    raise_forbidden,
    raise_service_unavailable,
    raise_too_many_requests,
)
from app.core.config import settings
from app.core.exceptions import (
    AiUsageLimitExceededError,
    ContentBlockedError,
    FirestoreServiceError,
    OpenAIServiceError,
)
from app.schemas.ai_ask import AiAskRequest, AiAskResponse
from app.schemas.ai_common import AiPersistence, BACKEND_OWNED_PERSISTENCE
from app.schemas.ai_photo import (
    AiPhotoAnalyzeRequest,
    AiPhotoAnalyzeResponse,
    AiPhotoIngredient,
)
from app.schemas.ai_text_meal import (
    AiTextMealAnalyzeRequest,
    AiTextMealAnalyzeResponse,
    AiTextMealIngredient,
)
from app.services import (
    ai_chat_prompt_service,
    ai_gateway_logger,
    ai_gateway_service,
    ai_usage_service,
    content_guard_service,
    openai_service,
    sanitization_service,
    text_meal_service,
)

router = APIRouter()
logger = logging.getLogger(__name__)


class AiResponseFields(TypedDict):
    usageCount: float
    remaining: float
    dateKey: str
    version: str
    persistence: AiPersistence


def _resolve_language(request: AiAskRequest) -> str:
    if request.context:
        for key in ("language", "lang"):
            value = request.context.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return "pl"


def _resolve_action_type(request: AiAskRequest) -> str:
    if request.context:
        for key in ("actionType", "action_type"):
            value = request.context.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return "chat"


def _get_local_answer(message: str, language: str) -> str:
    del message
    if language.lower() == "en":
        return "This request was handled locally."
    return "To zapytanie zostalo obsluzone lokalnie."


async def _log_gateway_result(
    *,
    user_id: str,
    action_type: str,
    message: str,
    language: str,
    result: ai_gateway_service.GatewayResult,
    response_time_ms: float | None = None,
    execution_time_ms: float | None = None,
    profile: str | None = None,
) -> None:
    try:
        ai_gateway_logger.log_gateway_decision(
            user_id,
            message,
            result,
            action_type,
            language=language,
            response_time_ms=response_time_ms,
            execution_time_ms=execution_time_ms,
            profile=profile,
        )
    except Exception:
        logger.exception(
            "Failed to persist AI gateway decision.",
            extra={"user_id": user_id, "action_type": action_type},
        )


async def _increment_usage_or_raise(
    user_id: str,
    *,
    cost: float = 1.0,
    include_cost_kwarg: bool = False,
) -> tuple[float, str, float]:
    try:
        if include_cost_kwarg:
            usage_count, _daily_limit, date_key, remaining = (
                await ai_usage_service.increment_usage(user_id, cost=cost)
            )
        else:
            usage_count, _daily_limit, date_key, remaining = (
                await ai_usage_service.increment_usage(user_id)
            )
    except AiUsageLimitExceededError as exc:
        raise_too_many_requests(exc, detail="AI usage limit exceeded")
    except FirestoreServiceError as exc:
        raise_database_error(exc)

    return usage_count, date_key, remaining


def _build_ai_response_fields(
    *,
    usage_count: float,
    remaining: float,
    date_key: str,
) -> AiResponseFields:
    return {
        "usageCount": usage_count,
        "remaining": remaining,
        "dateKey": date_key,
        "version": settings.VERSION,
        "persistence": BACKEND_OWNED_PERSISTENCE,
    }


def _build_ai_ask_response(
    *,
    reply: str,
    usage_count: float,
    remaining: float,
    date_key: str,
) -> AiAskResponse:
    return AiAskResponse(
        reply=reply,
        **_build_ai_response_fields(
            usage_count=usage_count,
            remaining=remaining,
            date_key=date_key,
        ),
    )


@router.post("/ai/ask", response_model=AiAskResponse)
async def ask_ai(
    request: AiAskRequest,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> AiAskResponse | JSONResponse:
    started_at = perf_counter()
    user_id = current_user.uid

    try:
        content_guard_service.check_allowed(request.message)
    except ContentBlockedError as exc:
        raise_forbidden(exc, detail=str(exc))

    language = _resolve_language(request)
    action_type = _resolve_action_type(request)
    gateway_result: ai_gateway_service.GatewayResult = {
        "decision": "FORWARD",
        "reason": "GATEWAY_DISABLED",
        "score": 1.0,
        "credit_cost": 1.0,
    }
    if settings.AI_GATEWAY_ENABLED and action_type == "chat":
        gateway_result = ai_gateway_service.evaluate_request(
            user_id,
            action_type,
            request.message,
            language=language,
        )
    elif action_type != "chat":
        gateway_result = {
            "decision": "FORWARD",
            "reason": "NON_CHAT_BYPASS",
            "score": 1.0,
            "credit_cost": 1.0,
        }

    sanitized_context = sanitization_service.sanitize_context(request.context)
    sanitized_message = sanitization_service.sanitize_request(
        request.message, sanitized_context
    )
    prompt_message = (
        ai_chat_prompt_service.build_chat_prompt(
            sanitized_message,
            sanitized_context,
            language=language,
        )
        if action_type == "chat"
        else sanitized_message
    )

    usage_count, date_key, remaining = await _increment_usage_or_raise(
        user_id,
        cost=gateway_result["credit_cost"],
        include_cost_kwarg=True,
    )

    if gateway_result["decision"] == "REJECT":
        await _log_gateway_result(
            user_id=user_id,
            action_type=action_type,
            message=request.message,
            language=language,
            result=gateway_result,
            execution_time_ms=(perf_counter() - started_at) * 1000,
        )
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "reason": gateway_result["reason"],
                "credit_cost": gateway_result["credit_cost"],
            },
        )

    if gateway_result["decision"] == "LOCAL_ANSWER":
        await _log_gateway_result(
            user_id=user_id,
            action_type=action_type,
            message=request.message,
            language=language,
            result=gateway_result,
            execution_time_ms=(perf_counter() - started_at) * 1000,
        )
        return _build_ai_ask_response(
            reply=_get_local_answer(request.message, language),
            usage_count=usage_count,
            remaining=remaining,
            date_key=date_key,
        )

    try:
        reply = await openai_service.ask_chat(prompt_message)
    except OpenAIServiceError as exc:
        raise_service_unavailable(exc, detail="AI service unavailable")

    await _log_gateway_result(
        user_id=user_id,
        action_type=action_type,
        message=request.message,
        language=language,
        result=gateway_result,
        response_time_ms=(perf_counter() - started_at) * 1000,
        execution_time_ms=(perf_counter() - started_at) * 1000,
    )

    return _build_ai_ask_response(
        reply=reply,
        usage_count=usage_count,
        remaining=remaining,
        date_key=date_key,
    )


@router.post("/ai/photo/analyze", response_model=AiPhotoAnalyzeResponse)
async def analyze_photo_ai(
    request: AiPhotoAnalyzeRequest,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> AiPhotoAnalyzeResponse:
    user_id = current_user.uid
    usage_count, date_key, remaining = await _increment_usage_or_raise(user_id)

    try:
        ingredients = await openai_service.analyze_photo(
            request.imageBase64,
            lang=request.lang,
        )
    except OpenAIServiceError as exc:
        raise_service_unavailable(exc, detail="AI service unavailable")

    return AiPhotoAnalyzeResponse(
        ingredients=[AiPhotoIngredient(**ingredient) for ingredient in ingredients],
        **_build_ai_response_fields(
            usage_count=usage_count,
            remaining=remaining,
            date_key=date_key,
        ),
    )


@router.post("/ai/text-meal/analyze", response_model=AiTextMealAnalyzeResponse)
async def analyze_text_meal_ai(
    request: AiTextMealAnalyzeRequest,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> AiTextMealAnalyzeResponse:
    user_id = current_user.uid
    usage_count, date_key, remaining = await _increment_usage_or_raise(user_id)

    try:
        ingredients = await text_meal_service.analyze_text_meal(
            request.payload,
            lang=request.lang,
        )
    except OpenAIServiceError as exc:
        raise_service_unavailable(exc, detail="AI service unavailable")

    return AiTextMealAnalyzeResponse(
        ingredients=[AiTextMealIngredient(**ingredient) for ingredient in ingredients],
        **_build_ai_response_fields(
            usage_count=usage_count,
            remaining=remaining,
            date_key=date_key,
        ),
    )
