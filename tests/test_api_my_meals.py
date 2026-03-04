from io import BytesIO

from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from app.core.exceptions import FirestoreServiceError
from app.main import app

client = TestClient(app)


def test_get_my_meal_changes_returns_backend_payload(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    list_changes = mocker.patch(
        "app.api.routes.my_meals.my_meal_service.list_changes",
        return_value=([], "2026-03-03T12:00:00.000Z|saved-1"),
    )

    response = client.get(
        "/api/v1/users/me/my-meals/changes?afterCursor=2026-03-01T00:00:00.000Z",
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "items": [],
        "nextCursor": "2026-03-03T12:00:00.000Z|saved-1",
    }
    list_changes.assert_called_once_with(
        "user-1",
        limit_count=100,
        after_cursor="2026-03-01T00:00:00.000Z",
    )


def test_post_my_meal_upsert_uses_backend_service(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    upsert_saved_meal = mocker.patch(
        "app.api.routes.my_meals.my_meal_service.upsert_saved_meal",
        return_value={
            "userUid": "user-1",
            "mealId": "saved-1",
            "timestamp": "2026-03-03T12:00:00.000Z",
            "type": "lunch",
            "name": "Saved meal",
            "ingredients": [],
            "createdAt": "2026-03-03T12:00:00.000Z",
            "updatedAt": "2026-03-03T12:00:00.000Z",
            "syncState": "synced",
            "source": "saved",
            "imageId": None,
            "photoUrl": None,
            "notes": None,
            "tags": [],
            "deleted": False,
            "cloudId": "saved-1",
            "totals": {"kcal": 200, "protein": 30, "carbs": 0, "fat": 5},
        },
    )

    response = client.post(
        "/api/v1/users/me/my-meals",
        json={
            "mealId": "saved-1",
            "timestamp": "2026-03-03T12:00:00.000Z",
            "type": "lunch",
            "ingredients": [],
        },
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json()["updated"] is True
    upsert_saved_meal.assert_called_once()


def test_post_my_meal_delete_uses_backend_service(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mark_deleted = mocker.patch(
        "app.api.routes.my_meals.my_meal_service.mark_deleted",
        return_value={
            "cloudId": "saved-1",
            "updatedAt": "2026-03-03T12:00:00.000Z",
        },
    )

    response = client.post(
        "/api/v1/users/me/my-meals/saved-1/delete",
        json={"updatedAt": "2026-03-03T12:00:00.000Z"},
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "mealId": "saved-1",
        "updatedAt": "2026-03-03T12:00:00.000Z",
        "deleted": True,
    }
    mark_deleted.assert_called_once_with(
        "user-1",
        "saved-1",
        updated_at="2026-03-03T12:00:00.000Z",
    )


def test_post_my_meal_photo_upload_uses_backend_service(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    upload_photo = mocker.patch(
        "app.api.routes.my_meals.my_meal_service.upload_photo",
        return_value={
            "mealId": "saved-1",
            "imageId": "image-1",
            "photoUrl": "https://cdn/saved-1.jpg",
        },
    )

    response = client.post(
        "/api/v1/users/me/my-meals/saved-1/photo",
        files={"file": ("saved-1.jpg", BytesIO(b"jpeg-bytes"), "image/jpeg")},
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "mealId": "saved-1",
        "imageId": "image-1",
        "photoUrl": "https://cdn/saved-1.jpg",
    }
    upload_photo.assert_called_once()


def test_get_my_meal_changes_returns_500_for_firestore_errors(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch(
        "app.api.routes.my_meals.my_meal_service.list_changes",
        side_effect=FirestoreServiceError("boom"),
    )

    response = client.get(
        "/api/v1/users/me/my-meals/changes",
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "Database error"}
