"""Integration tests for the AI usage endpoint using mocked service calls."""

from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from app.core.exceptions import FirestoreServiceError
from app.main import app

client = TestClient(app)


def test_get_ai_usage_returns_usage_payload(mocker: MockerFixture, auth_headers) -> None:
    mocker.patch(
        "app.api.routes.ai_usage.ai_usage_service.get_usage",
        return_value=(3, 20, "2026-03-02"),
    )

    response = client.get(
        "/api/v1/ai/usage",
        headers=auth_headers("abc"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "dateKey": "2026-03-02",
        "usageCount": 3.0,
        "dailyLimit": 20,
        "remaining": 17.0,
    }


def test_get_ai_usage_computes_remaining_from_limit_and_usage(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch(
        "app.api.routes.ai_usage.ai_usage_service.get_usage",
        return_value=(25, 20, "2026-03-02"),
    )

    response = client.get(
        "/api/v1/ai/usage",
        headers=auth_headers("abc"),
    )

    assert response.status_code == 200
    assert response.json()["remaining"] == -5.0


def test_get_ai_usage_returns_500_for_firestore_errors(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch(
        "app.api.routes.ai_usage.ai_usage_service.get_usage",
        side_effect=FirestoreServiceError("boom"),
    )

    response = client.get(
        "/api/v1/ai/usage",
        headers=auth_headers("abc"),
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "Failed to retrieve usage"}


def test_get_ai_usage_uses_uid_from_token(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    get_usage = mocker.patch(
        "app.api.routes.ai_usage.ai_usage_service.get_usage",
        return_value=(1, 20, "2026-03-02"),
    )

    response = client.get("/api/v1/ai/usage", headers=auth_headers("other-user"))

    assert response.status_code == 200
    assert response.json()["dateKey"] == "2026-03-02"
    get_usage.assert_called_once_with("other-user")
