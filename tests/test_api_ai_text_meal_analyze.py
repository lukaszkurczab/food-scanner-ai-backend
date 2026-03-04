"""Integration tests for the AI text meal analysis endpoint with mocked dependencies."""

from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from app.core.config import settings
from app.core.exceptions import (
    AiUsageLimitExceededError,
    FirestoreServiceError,
    OpenAIServiceError,
)
from app.main import app

client = TestClient(app)


def test_post_ai_text_meal_analyze_returns_ingredients_and_usage(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    increment_usage = mocker.patch(
        "app.api.routes.ai.ai_usage_service.increment_usage",
        return_value=(2.0, 20, "2026-03-03", 18.0),
    )
    analyze_text_meal = mocker.patch(
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
        json={
            "payload": {
                "name": "owsianka",
                "ingredients": "platki owsiane",
                "amount_g": 120,
                "notes": "na sniadanie",
            },
            "lang": "pl",
        },
        headers=auth_headers("abc"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "ingredients": [
            {
                "name": "Owsianka",
                "amount": 120.0,
                "protein": 6.0,
                "fat": 4.0,
                "carbs": 20.0,
                "kcal": 148.0,
                "unit": None,
            }
        ],
        "usageCount": 2.0,
        "remaining": 18.0,
        "dateKey": "2026-03-03",
        "version": settings.VERSION,
        "persistence": "backend_owned",
    }
    increment_usage.assert_called_once_with("abc")
    analyze_text_meal.assert_called_once()
    called_payload = analyze_text_meal.call_args.args[0]
    assert called_payload.name == "owsianka"
    assert called_payload.ingredients == "platki owsiane"
    assert called_payload.amount_g == 120
    assert called_payload.notes == "na sniadanie"
    assert analyze_text_meal.call_args.kwargs == {"lang": "pl"}


def test_post_ai_text_meal_analyze_requires_required_fields(auth_headers) -> None:
    response = client.post(
        "/api/v1/ai/text-meal/analyze",
        json={},
        headers=auth_headers("abc"),
    )

    assert response.status_code == 422


def test_post_ai_text_meal_analyze_returns_429_when_limit_is_exceeded(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch(
        "app.api.routes.ai.ai_usage_service.increment_usage",
        side_effect=AiUsageLimitExceededError("limit"),
    )

    response = client.post(
        "/api/v1/ai/text-meal/analyze",
        json={"payload": {"name": "burger"}},
        headers=auth_headers("abc"),
    )

    assert response.status_code == 429
    assert response.json() == {"detail": "AI usage limit exceeded"}


def test_post_ai_text_meal_analyze_returns_500_when_firestore_fails(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch(
        "app.api.routes.ai.ai_usage_service.increment_usage",
        side_effect=FirestoreServiceError("db down"),
    )

    response = client.post(
        "/api/v1/ai/text-meal/analyze",
        json={"payload": {"name": "burger"}},
        headers=auth_headers("abc"),
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "Database error"}


def test_post_ai_text_meal_analyze_returns_503_when_openai_fails(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch(
        "app.api.routes.ai.ai_usage_service.increment_usage",
        return_value=(1.0, 20, "2026-03-03", 19.0),
    )
    mocker.patch(
        "app.api.routes.ai.text_meal_service.analyze_text_meal",
        side_effect=OpenAIServiceError("unavailable"),
    )

    response = client.post(
        "/api/v1/ai/text-meal/analyze",
        json={"payload": {"name": "burger"}},
        headers=auth_headers("abc"),
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "AI service unavailable"}


def test_post_ai_text_meal_analyze_uses_uid_from_token(
    auth_headers,
    mocker: MockerFixture,
) -> None:
    increment_usage = mocker.patch(
        "app.api.routes.ai.ai_usage_service.increment_usage",
        return_value=(1.0, 20, "2026-03-03", 19.0),
    )
    mocker.patch(
        "app.api.routes.ai.text_meal_service.analyze_text_meal",
        return_value=[],
    )

    response = client.post(
        "/api/v1/ai/text-meal/analyze",
        json={"payload": {"name": "burger"}},
        headers=auth_headers("other-user"),
    )

    assert response.status_code == 200
    increment_usage.assert_called_once_with("other-user")
