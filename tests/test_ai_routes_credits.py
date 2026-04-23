"""Integration tests for legacy v1 AI analysis routes and credits behavior."""
from tests.types import AuthHeaders

from collections import deque
from datetime import datetime, timezone
from time import monotonic
from typing import Literal

import pytest
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from app.core.exceptions import OpenAIServiceError
import app.services.ai_gateway_service as _gw
from app.main import app
from app.schemas.ai_credits import AiCreditsStatus, CreditCosts

client = TestClient(app)


@pytest.fixture(autouse=True)
def _mock_rate_limit(mocker: MockerFixture) -> None:
    """Replace Firestore-backed rate limit with deterministic in-memory state."""
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


def test_post_ai_photo_analyze_returns_413_for_payload_guard(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
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


def test_post_ai_photo_analyze_deducts_five_credits(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
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
    auth_headers: AuthHeaders,
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
    auth_headers: AuthHeaders,
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
    auth_headers: AuthHeaders,
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
    auth_headers: AuthHeaders,
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
    auth_headers: AuthHeaders,
) -> None:
    deduct_credits = mocker.patch("app.api.routes.ai.ai_credits_service.deduct_credits")

    response = client.post(
        "/api/v1/ai/photo/analyze",
        json={},
        headers=auth_headers("abc"),
    )

    assert response.status_code == 422
    deduct_credits.assert_not_called()
