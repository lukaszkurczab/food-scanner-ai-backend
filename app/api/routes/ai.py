import logging
from datetime import datetime
from time import perf_counter
from typing import Literal, TypedDict

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import (
    AuthenticatedUser,
    get_required_authenticated_user,
)
from app.api.http_errors import (
    raise_service_unavailable,
)
from app.core.config import settings
from app.core.exceptions import (
    AiCreditsExhaustedError,
    OpenAIServiceError,
)
from app.schemas.ai_ask import AiAskRequest, AiAskResponse
from app.schemas.ai_common import AiPersistence, BACKEND_OWNED_PERSISTENCE
from app.schemas.ai_credits import AiCreditsStatus, CreditCosts
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
    ai_credits_service,
    ai_gateway_logger,
    ai_gateway_service,
    openai_service,
    sanitization_service,
    text_meal_service,
)

router = APIRouter()
logger = logging.getLogger(__name__)


class AiResponseFields(TypedDict):
    balance: int
    allocation: int
    tier: Literal["free", "premium"]
    periodStartAt: datetime
    periodEndAt: datetime
    costs: CreditCosts
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
    tier: Literal["free", "premium"] | None = None,
    credit_cost: float | None = None,
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
            tier=tier,
            credit_cost=credit_cost,
        )
    except Exception:
        logger.exception(
            "Failed to persist AI gateway decision.",
            extra={"user_id": user_id, "action_type": action_type},
        )


async def _deduct_credits_or_raise(
    *,
    user_id: str,
    cost: int,
    action: str,
) -> AiCreditsStatus:
    try:
        return await ai_credits_service.deduct_credits(user_id, cost=cost, action=action)
    except AiCreditsExhaustedError:
        credits_status = await ai_credits_service.get_credits_status(user_id)
        logger.warning(
            "AI credits exhausted for requested action.",
            extra={
                "user_id": user_id,
                "action": action,
                "credit_cost": cost,
                "tier": credits_status.tier,
                "balance": credits_status.balance,
                "allocation": credits_status.allocation,
                "period_end_at": credits_status.periodEndAt.isoformat(),
            },
        )
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "message": "AI credits exhausted",
                "code": "AI_CREDITS_EXHAUSTED",
                "credits": credits_status.model_dump(mode="json"),
            },
        )


async def _refund_credits_after_ai_failure(
    *,
    user_id: str,
    cost: int,
    action: str,
    endpoint: str,
) -> None:
    try:
        credits_status = await ai_credits_service.refund_credits(
            user_id,
            cost=cost,
            action=action,
        )
        logger.info(
            "Refunded AI credits after upstream failure.",
            extra={
                "user_id": user_id,
                "endpoint": endpoint,
                "cost": cost,
                "balance": credits_status.balance,
                "allocation": credits_status.allocation,
                "tier": credits_status.tier,
            },
        )
    except Exception:
        logger.exception(
            "Failed to refund AI credits after upstream failure.",
            extra={
                "user_id": user_id,
                "endpoint": endpoint,
                "cost": cost,
            },
        )


def _build_ai_response_fields_from_credits(
    *,
    credits_status: AiCreditsStatus,
) -> AiResponseFields:
    return {
        "balance": credits_status.balance,
        "allocation": credits_status.allocation,
        "tier": credits_status.tier,
        "periodStartAt": credits_status.periodStartAt,
        "periodEndAt": credits_status.periodEndAt,
        "costs": credits_status.costs,
        "version": settings.VERSION,
        "persistence": BACKEND_OWNED_PERSISTENCE,
    }


def _build_ai_ask_response(
    *,
    reply: str,
    credits_status: AiCreditsStatus,
) -> AiAskResponse:
    return AiAskResponse(
        reply=reply,
        **_build_ai_response_fields_from_credits(credits_status=credits_status),
    )


@router.post("/ai/ask", response_model=AiAskResponse)
async def ask_ai(
    request: AiAskRequest,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> AiAskResponse:
    started_at = perf_counter()
    user_id = current_user.uid

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
    if gateway_result["decision"] != "FORWARD":
        tier: Literal["free", "premium"] | None = None
        try:
            tier = (await ai_credits_service.get_credits_status(user_id)).tier
        except Exception:
            logger.exception(
                "Failed to resolve AI tier for gateway reject log.",
                extra={"user_id": user_id, "action_type": action_type},
            )
        await _log_gateway_result(
            user_id=user_id,
            action_type=action_type,
            message=request.message,
            language=language,
            result=gateway_result,
            response_time_ms=(perf_counter() - started_at) * 1000,
            execution_time_ms=(perf_counter() - started_at) * 1000,
            tier=tier,
            credit_cost=0.0,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "message": "AI request blocked by gateway",
                "code": "AI_GATEWAY_BLOCKED",
                "reason": gateway_result["reason"],
                "score": gateway_result["score"],
            },
        )

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

    credits_status = await _deduct_credits_or_raise(
        user_id=user_id,
        cost=settings.AI_CREDIT_COST_CHAT,
        action="chat",
    )

    try:
        reply = await openai_service.ask_chat(prompt_message)
    except OpenAIServiceError as exc:
        await _refund_credits_after_ai_failure(
            user_id=user_id,
            cost=settings.AI_CREDIT_COST_CHAT,
            action="chat_failure_refund",
            endpoint="/ai/ask",
        )
        raise_service_unavailable(exc, detail="AI service unavailable")

    await _log_gateway_result(
        user_id=user_id,
        action_type=action_type,
        message=request.message,
        language=language,
        result=gateway_result,
        response_time_ms=(perf_counter() - started_at) * 1000,
        execution_time_ms=(perf_counter() - started_at) * 1000,
        profile=credits_status.tier,
        tier=credits_status.tier,
        credit_cost=float(settings.AI_CREDIT_COST_CHAT),
    )

    return _build_ai_ask_response(
        reply=reply,
        credits_status=credits_status,
    )


@router.post("/ai/photo/analyze", response_model=AiPhotoAnalyzeResponse)
async def analyze_photo_ai(
    request: AiPhotoAnalyzeRequest,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> AiPhotoAnalyzeResponse:
    user_id = current_user.uid
    credits_status = await _deduct_credits_or_raise(
        user_id=user_id,
        cost=settings.AI_CREDIT_COST_PHOTO,
        action="photo_analysis",
    )

    try:
        ingredients = await openai_service.analyze_photo(
            request.imageBase64,
            lang=request.lang,
        )
    except OpenAIServiceError as exc:
        await _refund_credits_after_ai_failure(
            user_id=user_id,
            cost=settings.AI_CREDIT_COST_PHOTO,
            action="photo_analysis_failure_refund",
            endpoint="/ai/photo/analyze",
        )
        raise_service_unavailable(exc, detail="AI service unavailable")

    return AiPhotoAnalyzeResponse(
        ingredients=[AiPhotoIngredient(**ingredient) for ingredient in ingredients],
        **_build_ai_response_fields_from_credits(credits_status=credits_status),
    )


@router.post("/ai/text-meal/analyze", response_model=AiTextMealAnalyzeResponse)
async def analyze_text_meal_ai(
    request: AiTextMealAnalyzeRequest,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> AiTextMealAnalyzeResponse:
    user_id = current_user.uid
    credits_status = await _deduct_credits_or_raise(
        user_id=user_id,
        cost=settings.AI_CREDIT_COST_TEXT_MEAL,
        action="text_meal_analysis",
    )

    try:
        ingredients = await text_meal_service.analyze_text_meal(
            request.payload,
            lang=request.lang,
        )
    except OpenAIServiceError as exc:
        await _refund_credits_after_ai_failure(
            user_id=user_id,
            cost=settings.AI_CREDIT_COST_TEXT_MEAL,
            action="text_meal_analysis_failure_refund",
            endpoint="/ai/text-meal/analyze",
        )
        raise_service_unavailable(exc, detail="AI service unavailable")

    return AiTextMealAnalyzeResponse(
        ingredients=[AiTextMealIngredient(**ingredient) for ingredient in ingredients],
        **_build_ai_response_fields_from_credits(credits_status=credits_status),
    )
