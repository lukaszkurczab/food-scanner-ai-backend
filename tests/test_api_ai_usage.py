"""Integration tests for the AI usage endpoint using mocked service calls."""

from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from app.core.exceptions import FirestoreServiceError
from app.main import app

client = TestClient(app)


def test_get_ai_usage_returns_usage_payload(mocker: MockerFixture) -> None:
    mocker.patch(
        "app.api.routes.ai_usage.ai_usage_service.get_usage",
        return_value=(3, 20, "2026-03-02"),
    )

    response = client.get("/api/v1/ai/usage", params={"userId": "abc"})

    assert response.status_code == 200
    assert response.json() == {
        "userId": "abc",
        "dateKey": "2026-03-02",
        "usageCount": 3,
        "dailyLimit": 20,
        "remaining": 17,
    }


def test_get_ai_usage_requires_user_id() -> None:
    response = client.get("/api/v1/ai/usage")

    assert response.status_code == 422


def test_get_ai_usage_computes_remaining_from_limit_and_usage(mocker: MockerFixture) -> None:
    mocker.patch(
        "app.api.routes.ai_usage.ai_usage_service.get_usage",
        return_value=(25, 20, "2026-03-02"),
    )

    response = client.get("/api/v1/ai/usage", params={"userId": "abc"})

    assert response.status_code == 200
    assert response.json()["remaining"] == -5


def test_get_ai_usage_returns_500_for_firestore_errors(mocker: MockerFixture) -> None:
    mocker.patch(
        "app.api.routes.ai_usage.ai_usage_service.get_usage",
        side_effect=FirestoreServiceError("boom"),
    )

    response = client.get("/api/v1/ai/usage", params={"userId": "abc"})

    assert response.status_code == 500
    assert response.json() == {"detail": "Failed to retrieve usage"}
