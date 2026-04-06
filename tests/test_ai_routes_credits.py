"""Integration tests for AI routes using the AI credits system."""

from collections import deque
from datetime import datetime, timezone
from time import monotonic
from typing import Literal

import pytest
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

import app.services.ai_gateway_service as _gw
from app.core.config import settings
from app.core.exceptions import AiCreditsExhaustedError, OpenAIServiceError
from app.main import app
from app.schemas.ai_credits import AiCreditsStatus, CreditCosts

client = TestClient(app)


@pytest.fixture(autouse=True)
def _mock_rate_limit(mocker: MockerFixture) -> None:
    """Replace the Firestore-backed rate limiter with a fast in-memory one.

    This keeps route integration tests self-contained and fast.  Tests that
    need to exercise the rate-limit path can still patch RATE_LIMIT_MAX_REQUESTS
    to a low value and the in-memory bucket will respect it.
    """
    buckets: dict[str, deque[float]] = {}

    async def _in_memory_slot(user_id: str) -> bool:
        now = monotonic()
        bucket = buckets.setdefault(user_id, deque())
        while bucket and now - bucket[0] >= _gw.RATE_LIMIT_WINDOW_SECONDS:
            bucket.popleft()
        if len(bucket) >= _gw.RATE_LIMIT_MAX_REQUESTS:
            return False
        bucket.append(now)
        return True

    mocker.patch(
        "app.services.ai_gateway_service._consume_rate_limit_slot",
        side_effect=_in_memory_slot,
    )


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
        "app.api.routes.ai._execute_chat_completion",
        return_value=("Try grilled chicken with rice.", 24),
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
        "model": None,
        "runId": None,
        "confidence": None,
        "warnings": [],
    }
    deduct_credits.assert_called_once_with("abc", cost=1, action="chat")
    ask_chat.assert_called_once_with("chat prompt")
    log_gateway_decision.assert_called_once()
    logged_kwargs = log_gateway_decision.call_args.kwargs
    assert logged_kwargs["tier"] == "free"
    assert logged_kwargs["credit_cost"] == 1.0


def test_post_ai_ask_logs_gateway_observability_metadata(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    log_gateway_decision = mocker.patch(
        "app.api.routes.ai.ai_gateway_logger.log_gateway_decision"
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
        "app.api.routes.ai._execute_chat_completion",
        return_value=("Weather is out of scope, but here is a dinner tip.", 31),
    )

    response = client.post(
        "/api/v1/ai/ask",
        json={"message": "Ile bialka ma kurczak z ryzem?"},
        headers=auth_headers("abc"),
    )

    assert response.status_code == 200
    assert response.json()["model"] == "gpt-4o-mini"
    assert response.json()["runId"]
    assert response.json()["confidence"] is None
    assert response.json()["warnings"] == []
    log_gateway_decision.assert_called_once()
    gateway_result = log_gateway_decision.call_args.args[2]
    assert gateway_result["decision"] == "FORWARD"
    assert gateway_result["reason"] == "PASS_THROUGH"
    assert gateway_result["task_type"] == "chat"
    assert gateway_result["model"] == "gpt-4o-mini"
    assert gateway_result["estimated_tokens"] > 0
    assert gateway_result["estimated_cost"] == 1.0
    assert gateway_result["actual_tokens"] == 31
    assert gateway_result["outcome"] == "FORWARDED"
    assert gateway_result["request_id"]


def test_post_ai_ask_rejects_real_off_topic_chat(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    log_gateway_decision = mocker.patch(
        "app.api.routes.ai.ai_gateway_logger.log_gateway_decision"
    )
    get_credits_status = mocker.patch(
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
    deduct_credits = mocker.patch("app.api.routes.ai.ai_credits_service.deduct_credits")
    ask_chat = mocker.patch("app.api.routes.ai._execute_chat_completion")

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
            "score": 1.0,
        }
    }
    get_credits_status.assert_called_once_with("abc")
    deduct_credits.assert_not_called()
    ask_chat.assert_not_called()
    log_gateway_decision.assert_called_once()
    gateway_result = log_gateway_decision.call_args.args[2]
    assert gateway_result["decision"] == "REJECT"
    assert gateway_result["reason"] == "OFF_TOPIC"
    assert gateway_result["outcome"] == "REJECTED"
    assert gateway_result["enforced"] is True


def test_post_ai_ask_returns_429_when_gateway_rate_limit_is_hit(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    log_gateway_decision = mocker.patch(
        "app.api.routes.ai.ai_gateway_logger.log_gateway_decision"
    )
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
        "app.api.routes.ai._execute_chat_completion",
        return_value=("Jogurt ma sporo bialka.", 18),
    )
    mocker.patch("app.api.routes.ai.ai_gateway_service.RATE_LIMIT_MAX_REQUESTS", 1)
    ai_gateway_service = __import__(
        "app.api.routes.ai",
        fromlist=["ai_gateway_service"],
    ).ai_gateway_service
    ai_gateway_service.reset_rate_limit_state()

    first = client.post(
        "/api/v1/ai/ask",
        json={"message": "Ile bialka ma jogurt?"},
        headers=auth_headers("abc"),
    )
    second = client.post(
        "/api/v1/ai/ask",
        json={"message": "Ile bialka ma kefir?"},
        headers=auth_headers("abc"),
    )

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["detail"]["code"] == "AI_GATEWAY_RATE_LIMITED"
    assert log_gateway_decision.call_args.args[2]["outcome"] == "REJECTED"


def test_post_ai_photo_analyze_returns_413_for_payload_guard(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    log_gateway_decision = mocker.patch(
        "app.api.routes.ai.ai_gateway_logger.log_gateway_decision"
    )
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
    mocker.patch("app.api.routes.ai.ai_gateway_service.MAX_PHOTO_PAYLOAD_CHARS", 5)

    response = client.post(
        "/api/v1/ai/photo/analyze",
        json={"imageBase64": "base64-image"},
        headers=auth_headers("abc"),
    )

    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "AI_GATEWAY_PAYLOAD_TOO_LARGE"
    assert log_gateway_decision.call_args.args[2]["outcome"] == "REJECTED"


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
    ask_chat = mocker.patch("app.api.routes.ai._execute_chat_completion")

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
    ask_chat = mocker.patch("app.api.routes.ai._execute_chat_completion")

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
    log_gateway_decision = mocker.patch("app.api.routes.ai.ai_gateway_logger.log_gateway_decision")
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
        "app.api.routes.ai._execute_chat_completion",
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
    log_gateway_decision.assert_called_once()
    gateway_result = log_gateway_decision.call_args.args[2]
    assert gateway_result["outcome"] == "UPSTREAM_ERROR"
    assert gateway_result["failure_reason"] == "OpenAIServiceError"
    refund_credits.assert_called_once_with(
        "abc",
        cost=1,
        action="chat_failure_refund",
    )


def test_post_ai_photo_analyze_deducts_five_credits(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    log_gateway_decision = mocker.patch("app.api.routes.ai.ai_gateway_logger.log_gateway_decision")
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
        "app.api.routes.ai._execute_photo_completion",
        return_value=(
            [
                {
                    "name": "Owsianka",
                    "amount": 120,
                    "protein": 6,
                    "fat": 4,
                    "carbs": 20,
                    "kcal": 148,
                }
            ],
            145,
        ),
    )

    response = client.post(
        "/api/v1/ai/photo/analyze",
        json={"imageBase64": "base64-image"},
        headers=auth_headers("abc"),
    )

    assert response.status_code == 200
    assert response.json()["balance"] == 95
    assert response.json()["costs"] == {"chat": 1, "textMeal": 1, "photo": 5}
    assert response.json()["model"] == "gpt-4o"
    assert response.json()["runId"]
    assert response.json()["warnings"] == []
    deduct_credits.assert_called_once_with("abc", cost=5, action="photo_analysis")
    log_gateway_decision.assert_called_once()
    gateway_result = log_gateway_decision.call_args.args[2]
    assert gateway_result["task_type"] == "photo_meal_analysis"
    assert gateway_result["outcome"] == "FORWARDED"


def test_post_ai_text_meal_analyze_deducts_one_credit(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    log_gateway_decision = mocker.patch("app.api.routes.ai.ai_gateway_logger.log_gateway_decision")
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
        "app.api.routes.ai._execute_text_meal_completion",
        return_value=(
            [
                {
                    "name": "Owsianka",
                    "amount": 120,
                    "protein": 6,
                    "fat": 4,
                    "carbs": 20,
                    "kcal": 148,
                }
            ],
            88,
        ),
    )

    response = client.post(
        "/api/v1/ai/text-meal/analyze",
        json={"payload": {"name": "owsianka"}},
        headers=auth_headers("abc"),
    )

    assert response.status_code == 200
    assert response.json()["balance"] == 799
    assert response.json()["model"] == "gpt-4o-mini"
    assert response.json()["runId"]
    assert response.json()["warnings"] == []
    deduct_credits.assert_called_once_with("abc", cost=1, action="text_meal_analysis")
    log_gateway_decision.assert_called_once()
    gateway_result = log_gateway_decision.call_args.args[2]
    assert gateway_result["task_type"] == "text_meal_analysis"
    assert gateway_result["outcome"] == "FORWARDED"


def test_post_ai_photo_analyze_respects_gateway_reject(
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
            "reason": "TEST_BLOCK",
            "score": 0.8,
            "credit_cost": 0.0,
        },
    )
    deduct_credits = mocker.patch("app.api.routes.ai.ai_credits_service.deduct_credits")
    analyze_photo = mocker.patch("app.api.routes.ai._execute_photo_completion")

    response = client.post(
        "/api/v1/ai/photo/analyze",
        json={"imageBase64": "base64-image"},
        headers=auth_headers("abc"),
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "AI_GATEWAY_BLOCKED"
    deduct_credits.assert_not_called()
    analyze_photo.assert_not_called()
    log_gateway_decision.assert_called_once()
    assert log_gateway_decision.call_args.args[2]["outcome"] == "REJECTED"


def test_post_ai_text_meal_analyze_respects_gateway_reject(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    log_gateway_decision = mocker.patch("app.api.routes.ai.ai_gateway_logger.log_gateway_decision")
    mocker.patch(
        "app.api.routes.ai.ai_credits_service.get_credits_status",
        return_value=_credits_status(
            user_id="abc",
            tier="premium",
            balance=800,
            allocation=800,
            period_start_at=datetime(2026, 4, 14, tzinfo=timezone.utc),
            period_end_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
        ),
    )
    mocker.patch(
        "app.api.routes.ai.ai_gateway_service.evaluate_request",
        return_value={
            "decision": "REJECT",
            "reason": "TEST_BLOCK",
            "score": 0.7,
            "credit_cost": 0.0,
        },
    )
    deduct_credits = mocker.patch("app.api.routes.ai.ai_credits_service.deduct_credits")
    analyze_text_meal = mocker.patch("app.api.routes.ai._execute_text_meal_completion")

    response = client.post(
        "/api/v1/ai/text-meal/analyze",
        json={"payload": {"name": "owsianka"}},
        headers=auth_headers("abc"),
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "AI_GATEWAY_BLOCKED"
    deduct_credits.assert_not_called()
    analyze_text_meal.assert_not_called()
    log_gateway_decision.assert_called_once()
    assert log_gateway_decision.call_args.args[2]["outcome"] == "REJECTED"


def test_post_ai_photo_analyze_logs_upstream_failure(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    log_gateway_decision = mocker.patch("app.api.routes.ai.ai_gateway_logger.log_gateway_decision")
    mocker.patch(
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
        "app.api.routes.ai._execute_photo_completion",
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
        "/api/v1/ai/photo/analyze",
        json={"imageBase64": "base64-image"},
        headers=auth_headers("abc"),
    )

    assert response.status_code == 503
    log_gateway_decision.assert_called_once()
    gateway_result = log_gateway_decision.call_args.args[2]
    assert gateway_result["outcome"] == "UPSTREAM_ERROR"
    assert gateway_result["failure_reason"] == "OpenAIServiceError"
    refund_credits.assert_called_once_with(
        "abc",
        cost=5,
        action="photo_analysis_failure_refund",
    )


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


def test_post_ai_ask_ignores_client_action_type_and_skips_logging_when_gateway_disabled(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    log_gateway_decision = mocker.patch("app.api.routes.ai.ai_gateway_logger.log_gateway_decision")
    mocker.patch("app.api.routes.ai.settings.AI_GATEWAY_ENABLED", False)
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
        "app.api.routes.ai.sanitization_service.sanitize_context",
        return_value=None,
    )
    mocker.patch(
        "app.api.routes.ai.sanitization_service.sanitize_request",
        return_value="sanitized prompt",
    )
    prompt_builder = mocker.patch(
        "app.api.routes.ai.ai_chat_prompt_service.build_chat_prompt",
        return_value="chat prompt",
    )
    execute_chat = mocker.patch(
        "app.api.routes.ai._execute_chat_completion",
        return_value=("Diet answer", 22),
    )

    response = client.post(
        "/api/v1/ai/ask",
        json={
            "message": "Suggest a dinner",
            "context": {"actionType": "photo_analysis", "language": "pl"},
        },
        headers=auth_headers("abc"),
    )

    assert response.status_code == 200
    prompt_builder.assert_called_once()
    execute_chat.assert_called_once_with("chat prompt")
    log_gateway_decision.assert_not_called()
