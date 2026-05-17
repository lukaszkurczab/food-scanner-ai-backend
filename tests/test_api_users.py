from unittest.mock import MagicMock

from fastapi import HTTPException
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from app.core.exceptions import FirestoreServiceError
from app.main import app
from app.services.user_account_service import (
    AvatarMetadataValidationError,
    EmailValidationError,
)
from tests.types import AuthHeaders

client = TestClient(app)


def test_get_user_profile_returns_401_without_token() -> None:
    response = client.get("/api/v1/users/me/profile")

    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required"}


def test_get_user_profile_returns_401_for_invalid_token(
    mock_auth_token_decoder: MagicMock,
) -> None:
    mock_auth_token_decoder.side_effect = HTTPException(
        status_code=401,
        detail="Invalid authentication credentials",
    )

    response = client.get(
        "/api/v1/users/me/profile",
        headers={"Authorization": "Bearer invalid-token"},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid authentication credentials"}


def test_post_email_pending_returns_updated_payload(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    set_email_pending = mocker.patch(
        "app.api.routes.users.user_account_service.set_email_pending",
        return_value="new@example.com",
    )

    response = client.post(
        "/api/v1/users/me/email-pending",
        json={"email": "new@example.com"},
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "emailPending": "new@example.com",
        "updated": True,
    }
    set_email_pending.assert_called_once_with("user-1", "new@example.com")


def test_get_user_profile_returns_backend_payload(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    canonical_profile = {"language": "pl"}
    get_user_profile_data = mocker.patch(
        "app.api.routes.users.user_account_service.get_user_profile_data",
        return_value={"uid": "user-1", "username": "neo", "profile": canonical_profile},
    )

    response = client.get("/api/v1/users/me/profile", headers=auth_headers("user-1"))

    assert response.status_code == 200
    assert response.json() == {
        "profile": {"uid": "user-1", "username": "neo", "profile": canonical_profile},
    }
    get_user_profile_data.assert_called_once_with("user-1", touch_last_login=True)


def test_get_user_profile_uses_token_uid_not_client_supplied_uid(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    get_user_profile_data = mocker.patch(
        "app.api.routes.users.user_account_service.get_user_profile_data",
        return_value={"uid": "token-user", "username": "neo"},
    )

    response = client.get(
        "/api/v1/users/me/profile?uid=attacker-user",
        headers=auth_headers("token-user"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "profile": {"uid": "token-user", "username": "neo"},
    }
    get_user_profile_data.assert_called_once_with(
        "token-user",
        touch_last_login=True,
    )


def test_post_user_profile_returns_updated_payload(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    canonical_profile = {"language": "pl"}
    upsert_user_profile_data = mocker.patch(
        "app.api.routes.users.user_account_service.upsert_user_profile_data",
        return_value={"uid": "user-1", "username": "neo", "profile": canonical_profile},
    )

    response = client.post(
        "/api/v1/users/me/profile",
        json={"profile": {"language": "pl"}},
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "profile": {"uid": "user-1", "username": "neo", "profile": canonical_profile},
        "updated": True,
    }
    upsert_user_profile_data.assert_called_once_with(
        "user-1",
        {"profile": {"language": "pl"}},
        auth_email=None,
    )


def test_post_user_profile_returns_422_for_unknown_profile_fields(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    upsert_user_profile_data = mocker.patch(
        "app.api.routes.users.user_account_service.upsert_user_profile_data",
        return_value={"uid": "user-1"},
    )

    response = client.post(
        "/api/v1/users/me/profile",
        json={"username": "neo"},
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 422
    assert "extra_forbidden" in str(response.json())
    upsert_user_profile_data.assert_not_called()


def test_post_user_profile_returns_422_for_empty_payload(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    upsert_user_profile_data = mocker.patch(
        "app.api.routes.users.user_account_service.upsert_user_profile_data",
        return_value={"uid": "user-1"},
    )

    response = client.post(
        "/api/v1/users/me/profile",
        json={},
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 422
    assert "must not be empty" in str(response.json())
    upsert_user_profile_data.assert_not_called()


def test_post_user_profile_returns_422_for_invalid_enum_value(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    upsert_user_profile_data = mocker.patch(
        "app.api.routes.users.user_account_service.upsert_user_profile_data",
        return_value={"uid": "user-1"},
    )

    response = client.post(
        "/api/v1/users/me/profile",
        json={"profile": {"nutritionProfile": {"unitsSystem": "si"}}},
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 422
    assert "unitsSystem" in str(response.json())
    upsert_user_profile_data.assert_not_called()


def test_post_email_pending_returns_400_for_invalid_email(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mocker.patch(
        "app.api.routes.users.user_account_service.set_email_pending",
        side_effect=EmailValidationError("Invalid email address."),
    )

    response = client.post(
        "/api/v1/users/me/email-pending",
        json={"email": "bad"},
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid email address."}


def test_post_email_pending_returns_500_for_firestore_errors(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mocker.patch(
        "app.api.routes.users.user_account_service.set_email_pending",
        side_effect=FirestoreServiceError("boom"),
    )

    response = client.post(
        "/api/v1/users/me/email-pending",
        json={"email": "new@example.com"},
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "Database error"}


def test_post_delete_user_returns_success(mocker: MockerFixture, auth_headers: AuthHeaders) -> None:
    delete_account_data = mocker.patch(
        "app.api.routes.users.user_account_service.delete_account_data",
        return_value=None,
    )

    response = client.post("/api/v1/users/me/delete", headers=auth_headers("user-1"))

    assert response.status_code == 200
    assert response.json() == {"deleted": True}
    delete_account_data.assert_called_once_with("user-1")


def test_post_avatar_metadata_returns_updated_payload(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    set_avatar_metadata = mocker.patch(
        "app.api.routes.users.user_account_service.set_avatar_metadata",
        return_value=("https://cdn/avatar.jpg", "2026-03-03T12:00:00Z"),
    )

    response = client.post(
        "/api/v1/users/me/avatar-metadata",
        json={"avatarUrl": "https://cdn/avatar.jpg"},
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "avatarUrl": "https://cdn/avatar.jpg",
        "avatarlastSyncedAt": "2026-03-03T12:00:00Z",
        "updated": True,
    }
    set_avatar_metadata.assert_called_once_with("user-1", "https://cdn/avatar.jpg")


def test_post_avatar_metadata_returns_400_for_invalid_url(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mocker.patch(
        "app.api.routes.users.user_account_service.set_avatar_metadata",
        side_effect=AvatarMetadataValidationError("Invalid avatar URL."),
    )

    response = client.post(
        "/api/v1/users/me/avatar-metadata",
        json={"avatarUrl": "file:///avatar.jpg"},
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid avatar URL."}


def test_post_avatar_upload_returns_updated_payload(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    upload_avatar = mocker.patch(
        "app.api.routes.users.user_account_service.upload_avatar",
        return_value=("https://cdn/avatar.jpg", "2026-03-03T12:00:00Z"),
    )

    response = client.post(
        "/api/v1/users/me/avatar",
        files={"file": ("avatar.jpg", b"avatar-bytes", "image/jpeg")},
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "avatarUrl": "https://cdn/avatar.jpg",
        "avatarlastSyncedAt": "2026-03-03T12:00:00Z",
        "updated": True,
    }
    upload_avatar.assert_called_once()
    assert upload_avatar.call_args.args[0] == "user-1"


def test_post_avatar_upload_returns_500_for_firestore_errors(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mocker.patch(
        "app.api.routes.users.user_account_service.upload_avatar",
        side_effect=FirestoreServiceError("boom"),
    )

    response = client.post(
        "/api/v1/users/me/avatar",
        files={"file": ("avatar.jpg", b"avatar-bytes", "image/jpeg")},
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "Database error"}


def test_get_user_export_returns_backend_payload(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    get_user_export_data = mocker.patch(
        "app.api.routes.users.user_account_service.get_user_export_data",
        return_value=(
            {"uid": "user-1", "username": "neo"},
            [{"id": "meal-1"}],
            [{"id": "saved-1"}],
            [{"id": "chat-1"}],
            [{"id": "notif-1"}],
            {"motivationEnabled": True},
            [{"id": "feedback-1"}],
        ),
    )

    response = client.get("/api/v1/users/me/export", headers=auth_headers("user-1"))

    assert response.status_code == 200
    assert response.json() == {
        "profile": {"uid": "user-1", "username": "neo"},
        "meals": [{"id": "meal-1"}],
        "myMeals": [{"id": "saved-1"}],
        "chatMessages": [{"id": "chat-1"}],
        "notifications": [{"id": "notif-1"}],
        "notificationPrefs": {"motivationEnabled": True},
        "feedback": [{"id": "feedback-1"}],
    }
    get_user_export_data.assert_called_once_with("user-1")


def test_get_user_export_returns_500_for_firestore_errors(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mocker.patch(
        "app.api.routes.users.user_account_service.get_user_export_data",
        side_effect=FirestoreServiceError("boom"),
    )

    response = client.get("/api/v1/users/me/export", headers=auth_headers("user-1"))

    assert response.status_code == 500
    assert response.json() == {"detail": "Database error"}


def test_post_delete_user_returns_500_for_firestore_errors(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mocker.patch(
        "app.api.routes.users.user_account_service.delete_account_data",
        side_effect=FirestoreServiceError("boom"),
    )

    response = client.post("/api/v1/users/me/delete", headers=auth_headers("user-1"))

    assert response.status_code == 500
    assert response.json() == {"detail": "Database error"}


def test_get_user_export_requires_authentication() -> None:
    response = client.get("/api/v1/users/me/export")

    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required"}
