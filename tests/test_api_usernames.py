from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from app.core.exceptions import FirestoreServiceError
from app.main import app
from app.services.username_service import (
    UsernameUnavailableError,
    UsernameValidationError,
)

client = TestClient(app)


def test_get_username_availability_returns_backend_payload(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    is_username_available = mocker.patch(
        "app.api.routes.usernames.username_service.is_username_available",
        return_value=("neo", True),
    )

    response = client.get(
        "/api/v1/usernames/availability",
        params={"username": "Neo"},
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json() == {"username": "neo", "available": True}
    is_username_available.assert_called_once_with("Neo", current_user_id="user-1")


def test_get_username_availability_returns_500_for_firestore_errors(
    mocker: MockerFixture,
) -> None:
    mocker.patch(
        "app.api.routes.usernames.username_service.is_username_available",
        side_effect=FirestoreServiceError("boom"),
    )

    response = client.get("/api/v1/usernames/availability", params={"username": "neo"})

    assert response.status_code == 500
    assert response.json() == {"detail": "Failed to retrieve username availability"}


def test_post_user_username_claim_returns_normalized_username(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    claim_username = mocker.patch(
        "app.api.routes.usernames.username_service.claim_username",
        return_value="trinity",
    )

    response = client.post(
        "/api/v1/users/me/username",
        json={"username": "Trinity"},
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "username": "trinity",
        "updated": True,
    }
    claim_username.assert_called_once_with("user-1", "Trinity")


def test_post_user_username_claim_returns_400_for_invalid_username(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch(
        "app.api.routes.usernames.username_service.claim_username",
        side_effect=UsernameValidationError("too short"),
    )

    response = client.post(
        "/api/v1/users/me/username",
        json={"username": "ab"},
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "too short"}


def test_post_user_username_claim_returns_409_when_taken(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch(
        "app.api.routes.usernames.username_service.claim_username",
        side_effect=UsernameUnavailableError("taken"),
    )

    response = client.post(
        "/api/v1/users/me/username",
        json={"username": "neo"},
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "Username unavailable"}


def test_post_user_username_claim_returns_500_for_firestore_errors(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch(
        "app.api.routes.usernames.username_service.claim_username",
        side_effect=FirestoreServiceError("boom"),
    )

    response = client.post(
        "/api/v1/users/me/username",
        json={"username": "neo"},
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "Database error"}
