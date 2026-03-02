"""Integration tests for the AI ask endpoint with fully mocked dependencies."""

from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from app.core.config import settings
from app.core.exceptions import (
    AiUsageLimitExceededError,
    ContentBlockedError,
    FirestoreServiceError,
    OpenAIServiceError,
)
from app.main import app

client = TestClient(app)


def test_post_ai_ask_returns_reply_and_usage(mocker: MockerFixture) -> None:
    check_allowed = mocker.patch("app.api.routes.ai.content_guard_service.check_allowed")
    sanitize_request = mocker.patch(
        "app.api.routes.ai.sanitization_service.sanitize_request",
        return_value="sanitized prompt",
    )
    increment_usage = mocker.patch(
        "app.api.routes.ai.ai_usage_service.increment_usage",
        return_value=(4, 20, "2026-03-02"),
    )
    ask_chat = mocker.patch(
        "app.api.routes.ai.openai_service.ask_chat",
        return_value="Try grilled chicken with rice.",
    )

    response = client.post(
        "/api/v1/ai/ask",
        json={
            "userId": "abc",
            "message": "Suggest a dinner",
            "context": {"weightKg": 78},
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "userId": "abc",
        "reply": "Try grilled chicken with rice.",
        "usageCount": 4,
        "remaining": 16,
        "dateKey": "2026-03-02",
        "version": settings.VERSION,
    }
    check_allowed.assert_called_once_with("Suggest a dinner")
    sanitize_request.assert_called_once_with("Suggest a dinner", {"weightKg": 78})
    increment_usage.assert_called_once_with("abc")
    ask_chat.assert_called_once_with("sanitized prompt")


def test_post_ai_ask_requires_required_fields() -> None:
    response = client.post("/api/v1/ai/ask", json={"userId": "abc"})

    assert response.status_code == 422


def test_post_ai_ask_returns_403_when_content_is_blocked(mocker: MockerFixture) -> None:
    mocker.patch(
        "app.api.routes.ai.content_guard_service.check_allowed",
        side_effect=ContentBlockedError("blocked"),
    )

    response = client.post(
        "/api/v1/ai/ask",
        json={"userId": "abc", "message": "therapy advice"},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "blocked"}


def test_post_ai_ask_returns_429_when_limit_is_exceeded(mocker: MockerFixture) -> None:
    mocker.patch("app.api.routes.ai.content_guard_service.check_allowed")
    mocker.patch(
        "app.api.routes.ai.sanitization_service.sanitize_request",
        return_value="sanitized prompt",
    )
    mocker.patch(
        "app.api.routes.ai.ai_usage_service.increment_usage",
        side_effect=AiUsageLimitExceededError("limit"),
    )

    response = client.post(
        "/api/v1/ai/ask",
        json={"userId": "abc", "message": "Suggest a dinner"},
    )

    assert response.status_code == 429
    assert response.json() == {"detail": "AI usage limit exceeded"}


def test_post_ai_ask_returns_500_when_firestore_fails(mocker: MockerFixture) -> None:
    mocker.patch("app.api.routes.ai.content_guard_service.check_allowed")
    mocker.patch(
        "app.api.routes.ai.sanitization_service.sanitize_request",
        return_value="sanitized prompt",
    )
    mocker.patch(
        "app.api.routes.ai.ai_usage_service.increment_usage",
        side_effect=FirestoreServiceError("db down"),
    )

    response = client.post(
        "/api/v1/ai/ask",
        json={"userId": "abc", "message": "Suggest a dinner"},
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "Database error"}


def test_post_ai_ask_returns_503_when_openai_fails(mocker: MockerFixture) -> None:
    mocker.patch("app.api.routes.ai.content_guard_service.check_allowed")
    mocker.patch(
        "app.api.routes.ai.sanitization_service.sanitize_request",
        return_value="sanitized prompt",
    )
    mocker.patch(
        "app.api.routes.ai.ai_usage_service.increment_usage",
        return_value=(1, 20, "2026-03-02"),
    )
    mocker.patch(
        "app.api.routes.ai.openai_service.ask_chat",
        side_effect=OpenAIServiceError("unavailable"),
    )

    response = client.post(
        "/api/v1/ai/ask",
        json={"userId": "abc", "message": "Suggest a dinner"},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "AI service unavailable"}
