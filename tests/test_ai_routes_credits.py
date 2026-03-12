"""Integration tests for AI routes using the AI credits system."""

from datetime import datetime, timezone
from typing import Literal

from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from app.core.config import settings
from app.core.exceptions import AiCreditsExhaustedError, OpenAIServiceError
from app.main import app
from app.schemas.ai_credits import AiCreditsStatus, CreditCosts

client = TestClient(app)


def _credits_status(
    *,
    user_id: str,
    tier: Literal["free", "premium"],
    balance: int,
    allocation: int,
    period_start_at: datetime,
    period_end_at: datetime,
) -> AiCreditsStatus:
    return AiCreditsStatus(
        userId=user_id,
        tier=tier,
        balance=balance,
        allocation=allocation,
        periodStartAt=period_start_at,
        periodEndAt=period_end_at,
        costs=CreditCosts(chat=1, textMeal=1, photo=5),
        renewalAnchorSource="rolling_monthly",
    )


def test_post_ai_ask_deducts_chat_credit_and_returns_credit_fields(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    log_gateway_decision = mocker.patch("app.api.routes.ai.ai_gateway_logger.log_gateway_decision")
    mocker.patch(
        "app.api.routes.ai.ai_gateway_service.evaluate_request",
        return_value={
            "decision": "FORWARD",
            "reason": "PASS_THROUGH",
            "score": 1.0,
            "credit_cost": 0.5,
        },
    )
    mocker.patch(
        "app.api.routes.ai.sanitization_service.sanitize_context",
        return_value=None,
    )
    mocker.patch(
        "app.api.routes.ai.sanitization_service.sanitize_request",
        return_value="sanitized prompt",
    )
    mocker.patch(
        "app.api.routes.ai.ai_chat_prompt_service.build_chat_prompt",
        return_value="chat prompt",
    )
    deduct_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.deduct_credits",
        return_value=_credits_status(
            user_id="abc",
            tier="free",
            balance=99,
            allocation=100,
            period_start_at=datetime(2026, 3, 23, tzinfo=timezone.utc),
            period_end_at=datetime(2026, 4, 23, tzinfo=timezone.utc),
        ),
    )
    ask_chat = mocker.patch(
        "app.api.routes.ai.openai_service.ask_chat",
        return_value="Try grilled chicken with rice.",
    )

    response = client.post(
        "/api/v1/ai/ask",
        json={"message": "Suggest a dinner"},
        headers=auth_headers("abc"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "reply": "Try grilled chicken with rice.",
        "balance": 99,
        "allocation": 100,
        "tier": "free",
        "periodStartAt": "2026-03-23T00:00:00Z",
        "periodEndAt": "2026-04-23T00:00:00Z",
        "costs": {"chat": 1, "textMeal": 1, "photo": 5},
        "version": settings.VERSION,
        "persistence": "backend_owned",
    }
    deduct_credits.assert_called_once_with("abc", cost=1, action="chat")
    ask_chat.assert_called_once_with("chat prompt")
    log_gateway_decision.assert_called_once()
    logged_kwargs = log_gateway_decision.call_args.kwargs
    assert logged_kwargs["tier"] == "free"
    assert logged_kwargs["credit_cost"] == 1.0


def test_post_ai_ask_returns_402_with_fresh_snapshot_when_credits_exhausted(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch("app.api.routes.ai.ai_gateway_logger.log_gateway_decision")
    exhausted_warning = mocker.patch("app.api.routes.ai.logger.warning")
    mocker.patch(
        "app.api.routes.ai.ai_gateway_service.evaluate_request",
        return_value={
            "decision": "FORWARD",
            "reason": "PASS_THROUGH",
            "score": 1.0,
            "credit_cost": 1.0,
        },
    )
    mocker.patch(
        "app.api.routes.ai.sanitization_service.sanitize_context",
        return_value=None,
    )
    mocker.patch(
        "app.api.routes.ai.sanitization_service.sanitize_request",
        return_value="sanitized prompt",
    )
    mocker.patch(
        "app.api.routes.ai.ai_chat_prompt_service.build_chat_prompt",
        return_value="chat prompt",
    )
    deduct_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.deduct_credits",
        side_effect=AiCreditsExhaustedError("no credits"),
    )
    get_credits_status = mocker.patch(
        "app.api.routes.ai.ai_credits_service.get_credits_status",
        return_value=_credits_status(
            user_id="abc",
            tier="free",
            balance=0,
            allocation=100,
            period_start_at=datetime(2026, 3, 23, tzinfo=timezone.utc),
            period_end_at=datetime(2026, 4, 23, tzinfo=timezone.utc),
        ),
    )
    ask_chat = mocker.patch("app.api.routes.ai.openai_service.ask_chat")

    response = client.post(
        "/api/v1/ai/ask",
        json={"message": "Suggest a dinner"},
        headers=auth_headers("abc"),
    )

    assert response.status_code == 402
    assert response.json() == {
        "detail": {
            "message": "AI credits exhausted",
            "code": "AI_CREDITS_EXHAUSTED",
            "credits": {
                "userId": "abc",
                "tier": "free",
                "balance": 0,
                "allocation": 100,
                "periodStartAt": "2026-03-23T00:00:00Z",
                "periodEndAt": "2026-04-23T00:00:00Z",
                "costs": {"chat": 1, "textMeal": 1, "photo": 5},
                "renewalAnchorSource": "rolling_monthly",
                "revenueCatEntitlementId": None,
                "revenueCatExpirationAt": None,
                "lastRevenueCatEventId": None,
            },
        }
    }
    deduct_credits.assert_called_once_with("abc", cost=1, action="chat")
    get_credits_status.assert_called_once_with("abc")
    ask_chat.assert_not_called()
    exhausted_warning.assert_called_once_with(
        "AI credits exhausted for requested action.",
        extra={
            "user_id": "abc",
            "action": "chat",
            "credit_cost": 1,
            "tier": "free",
            "balance": 0,
            "allocation": 100,
            "period_end_at": "2026-04-23T00:00:00+00:00",
        },
    )


def test_post_ai_ask_gateway_reject_has_zero_deduction(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    log_gateway_decision = mocker.patch("app.api.routes.ai.ai_gateway_logger.log_gateway_decision")
    mocker.patch(
        "app.api.routes.ai.ai_credits_service.get_credits_status",
        return_value=_credits_status(
            user_id="abc",
            tier="free",
            balance=100,
            allocation=100,
            period_start_at=datetime(2026, 3, 23, tzinfo=timezone.utc),
            period_end_at=datetime(2026, 4, 23, tzinfo=timezone.utc),
        ),
    )
    mocker.patch(
        "app.api.routes.ai.ai_gateway_service.evaluate_request",
        return_value={
            "decision": "REJECT",
            "reason": "OFF_TOPIC",
            "score": 0.2,
            "credit_cost": 0.0,
        },
    )
    deduct_credits = mocker.patch("app.api.routes.ai.ai_credits_service.deduct_credits")
    ask_chat = mocker.patch("app.api.routes.ai.openai_service.ask_chat")

    response = client.post(
        "/api/v1/ai/ask",
        json={"message": "Jaka bedzie pogoda jutro?"},
        headers=auth_headers("abc"),
    )

    assert response.status_code == 400
    assert response.json() == {
        "detail": {
            "message": "AI request blocked by gateway",
            "code": "AI_GATEWAY_BLOCKED",
            "reason": "OFF_TOPIC",
            "score": 0.2,
        }
    }
    deduct_credits.assert_not_called()
    ask_chat.assert_not_called()
    log_gateway_decision.assert_called_once()
    logged_kwargs = log_gateway_decision.call_args.kwargs
    assert logged_kwargs["tier"] == "free"
    assert logged_kwargs["credit_cost"] == 0.0


def test_post_ai_ask_refunds_credits_after_ai_failure(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch("app.api.routes.ai.ai_gateway_logger.log_gateway_decision")
    mocker.patch(
        "app.api.routes.ai.ai_gateway_service.evaluate_request",
        return_value={
            "decision": "FORWARD",
            "reason": "PASS_THROUGH",
            "score": 1.0,
            "credit_cost": 1.0,
        },
    )
    mocker.patch(
        "app.api.routes.ai.sanitization_service.sanitize_context",
        return_value=None,
    )
    mocker.patch(
        "app.api.routes.ai.sanitization_service.sanitize_request",
        return_value="sanitized prompt",
    )
    mocker.patch(
        "app.api.routes.ai.ai_chat_prompt_service.build_chat_prompt",
        return_value="chat prompt",
    )
    mocker.patch(
        "app.api.routes.ai.ai_credits_service.deduct_credits",
        return_value=_credits_status(
            user_id="abc",
            tier="free",
            balance=99,
            allocation=100,
            period_start_at=datetime(2026, 3, 23, tzinfo=timezone.utc),
            period_end_at=datetime(2026, 4, 23, tzinfo=timezone.utc),
        ),
    )
    mocker.patch(
        "app.api.routes.ai.openai_service.ask_chat",
        side_effect=OpenAIServiceError("unavailable"),
    )
    refund_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.refund_credits",
        return_value=_credits_status(
            user_id="abc",
            tier="free",
            balance=100,
            allocation=100,
            period_start_at=datetime(2026, 3, 23, tzinfo=timezone.utc),
            period_end_at=datetime(2026, 4, 23, tzinfo=timezone.utc),
        ),
    )

    response = client.post(
        "/api/v1/ai/ask",
        json={"message": "Suggest a dinner"},
        headers=auth_headers("abc"),
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "AI service unavailable"}
    refund_credits.assert_called_once_with(
        "abc",
        cost=1,
        action="chat_failure_refund",
    )


def test_post_ai_photo_analyze_deducts_five_credits(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    deduct_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.deduct_credits",
        return_value=_credits_status(
            user_id="abc",
            tier="free",
            balance=95,
            allocation=100,
            period_start_at=datetime(2026, 3, 23, tzinfo=timezone.utc),
            period_end_at=datetime(2026, 4, 23, tzinfo=timezone.utc),
        ),
    )
    mocker.patch(
        "app.api.routes.ai.openai_service.analyze_photo",
        return_value=[
            {
                "name": "Owsianka",
                "amount": 120,
                "protein": 6,
                "fat": 4,
                "carbs": 20,
                "kcal": 148,
            }
        ],
    )

    response = client.post(
        "/api/v1/ai/photo/analyze",
        json={"imageBase64": "base64-image"},
        headers=auth_headers("abc"),
    )

    assert response.status_code == 200
    assert response.json()["balance"] == 95
    assert response.json()["costs"] == {"chat": 1, "textMeal": 1, "photo": 5}
    deduct_credits.assert_called_once_with("abc", cost=5, action="photo_analysis")


def test_post_ai_text_meal_analyze_deducts_one_credit(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    deduct_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.deduct_credits",
        return_value=_credits_status(
            user_id="abc",
            tier="premium",
            balance=799,
            allocation=800,
            period_start_at=datetime(2026, 4, 14, tzinfo=timezone.utc),
            period_end_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
        ),
    )
    mocker.patch(
        "app.api.routes.ai.text_meal_service.analyze_text_meal",
        return_value=[
            {
                "name": "Owsianka",
                "amount": 120,
                "protein": 6,
                "fat": 4,
                "carbs": 20,
                "kcal": 148,
            }
        ],
    )

    response = client.post(
        "/api/v1/ai/text-meal/analyze",
        json={"payload": {"name": "owsianka"}},
        headers=auth_headers("abc"),
    )

    assert response.status_code == 200
    assert response.json()["balance"] == 799
    deduct_credits.assert_called_once_with("abc", cost=1, action="text_meal_analysis")


def test_post_ai_photo_validation_reject_has_zero_deduction(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    deduct_credits = mocker.patch("app.api.routes.ai.ai_credits_service.deduct_credits")

    response = client.post(
        "/api/v1/ai/photo/analyze",
        json={},
        headers=auth_headers("abc"),
    )

    assert response.status_code == 422
    deduct_credits.assert_not_called()
