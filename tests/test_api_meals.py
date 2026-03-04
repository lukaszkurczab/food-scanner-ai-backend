from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from app.core.exceptions import FirestoreServiceError
from app.main import app

client = TestClient(app)


def test_get_meals_history_returns_backend_payload(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    list_history = mocker.patch(
        "app.api.routes.meals.meal_service.list_history",
        return_value=(
            [
                {
                    "userUid": "user-1",
                    "mealId": "meal-1",
                    "timestamp": "2026-03-03T12:00:00.000Z",
                    "type": "lunch",
                    "name": "Chicken",
                    "ingredients": [],
                    "createdAt": "2026-03-03T12:00:00.000Z",
                    "updatedAt": "2026-03-03T12:00:00.000Z",
                    "syncState": "synced",
                    "source": "manual",
                    "imageId": None,
                    "photoUrl": None,
                    "notes": None,
                    "tags": [],
                    "deleted": False,
                    "cloudId": "meal-1",
                    "totals": {"kcal": 200, "protein": 30, "carbs": 0, "fat": 5},
                }
            ],
            "2026-03-03T12:00:00.000Z|meal-1",
        ),
    )

    response = client.get(
        "/api/v1/users/me/meals/history?limit=10&caloriesMin=100&caloriesMax=500",
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json()["nextCursor"] == "2026-03-03T12:00:00.000Z|meal-1"
    list_history.assert_called_once_with(
        "user-1",
        limit_count=10,
        before_cursor=None,
        calories=(100.0, 500.0),
        protein=None,
        carbs=None,
        fat=None,
        timestamp_start=None,
        timestamp_end=None,
    )


def test_get_meal_changes_returns_backend_payload(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    list_changes = mocker.patch(
        "app.api.routes.meals.meal_service.list_changes",
        return_value=([], "2026-03-03T12:00:00.000Z|meal-1"),
    )

    response = client.get(
        "/api/v1/users/me/meals/changes?afterCursor=2026-03-01T00:00:00.000Z",
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "items": [],
        "nextCursor": "2026-03-03T12:00:00.000Z|meal-1",
    }
    list_changes.assert_called_once_with(
        "user-1",
        limit_count=100,
        after_cursor="2026-03-01T00:00:00.000Z",
    )


def test_get_meal_photo_url_returns_backend_payload(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    resolve_photo = mocker.patch(
        "app.api.routes.meals.meal_service.resolve_photo",
        return_value={
            "mealId": "meal-1",
            "imageId": "image-1",
            "photoUrl": "https://cdn/meal.jpg",
        },
    )

    response = client.get(
        "/api/v1/users/me/meals/photo-url?mealId=meal-1&imageId=image-1",
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "mealId": "meal-1",
        "imageId": "image-1",
        "photoUrl": "https://cdn/meal.jpg",
    }
    resolve_photo.assert_called_once_with(
        "user-1",
        meal_id="meal-1",
        image_id="image-1",
    )


def test_post_meal_photo_upload_returns_backend_payload(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    upload_photo = mocker.patch(
        "app.api.routes.meals.meal_service.upload_photo",
        return_value={
            "imageId": "image-1",
            "photoUrl": "https://cdn/meal.jpg",
        },
    )

    response = client.post(
        "/api/v1/users/me/meals/photo",
        files={"file": ("meal.jpg", b"meal-bytes", "image/jpeg")},
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "mealId": None,
        "imageId": "image-1",
        "photoUrl": "https://cdn/meal.jpg",
    }
    upload_photo.assert_called_once()


def test_post_meal_upsert_persists_via_backend_service(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    upsert_meal = mocker.patch(
        "app.api.routes.meals.meal_service.upsert_meal",
        return_value={
            "userUid": "user-1",
            "mealId": "meal-1",
            "timestamp": "2026-03-03T12:00:00.000Z",
            "type": "lunch",
            "name": "Chicken",
            "ingredients": [],
            "createdAt": "2026-03-03T12:00:00.000Z",
            "updatedAt": "2026-03-03T12:00:00.000Z",
            "syncState": "synced",
            "source": "manual",
            "imageId": None,
            "photoUrl": None,
            "notes": None,
            "tags": [],
            "deleted": False,
            "cloudId": "meal-1",
            "totals": {"kcal": 200, "protein": 30, "carbs": 0, "fat": 5},
        },
    )

    response = client.post(
        "/api/v1/users/me/meals",
        json={
            "mealId": "meal-1",
            "timestamp": "2026-03-03T12:00:00.000Z",
            "type": "lunch",
            "ingredients": [],
        },
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json()["updated"] is True
    upsert_meal.assert_called_once()


def test_post_meal_delete_uses_backend_service(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mark_deleted = mocker.patch(
        "app.api.routes.meals.meal_service.mark_deleted",
        return_value={
            "cloudId": "meal-1",
            "updatedAt": "2026-03-03T12:00:00.000Z",
        },
    )

    response = client.post(
        "/api/v1/users/me/meals/meal-1/delete",
        json={"updatedAt": "2026-03-03T12:00:00.000Z"},
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "mealId": "meal-1",
        "updatedAt": "2026-03-03T12:00:00.000Z",
        "deleted": True,
    }
    mark_deleted.assert_called_once_with(
        "user-1",
        "meal-1",
        updated_at="2026-03-03T12:00:00.000Z",
    )


def test_get_meals_history_returns_500_for_firestore_errors(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch(
        "app.api.routes.meals.meal_service.list_history",
        side_effect=FirestoreServiceError("boom"),
    )

    response = client.get(
        "/api/v1/users/me/meals/history",
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "Database error"}


def test_get_meal_photo_url_returns_400_for_missing_identifiers(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch(
        "app.api.routes.meals.meal_service.resolve_photo",
        side_effect=ValueError("Missing meal photo identifier"),
    )

    response = client.get(
        "/api/v1/users/me/meals/photo-url",
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Missing meal photo identifier"}
