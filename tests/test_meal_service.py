import asyncio

from pytest_mock import MockerFixture

from app.services import meal_service


def _build_snapshot(
    mocker: MockerFixture,
    *,
    exists: bool,
    data: dict[str, object] | None = None,
):
    snapshot = mocker.Mock()
    snapshot.exists = exists
    snapshot.id = str((data or {}).get("cloudId") or (data or {}).get("mealId") or "meal-1")
    snapshot.to_dict.return_value = data or {}
    return snapshot


def test_upsert_meal_keeps_newer_remote_document(mocker: MockerFixture) -> None:
    client = mocker.Mock()
    users_collection = mocker.Mock()
    user_ref = mocker.Mock()
    meals_collection = mocker.Mock()
    meal_ref = mocker.Mock()

    client.collection.return_value = users_collection
    users_collection.document.return_value = user_ref
    user_ref.collection.return_value = meals_collection
    meals_collection.document.return_value = meal_ref
    meal_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={
            "cloudId": "meal-1",
            "mealId": "meal-1",
            "userUid": "user-1",
            "timestamp": "2026-03-03T12:00:00.000Z",
            "dayKey": "2026-03-03",
            "type": "lunch",
            "ingredients": [],
            "createdAt": "2026-03-03T12:00:00.000Z",
            "updatedAt": "2026-03-03T13:00:00.000Z",
            "deleted": False,
            "totals": {"kcal": 200, "protein": 30, "carbs": 0, "fat": 5},
        },
    )
    mocker.patch("app.services.meal_service.get_firestore", return_value=client)
    sync_streak = mocker.patch("app.services.meal_service.streak_service.sync_streak_from_meals")

    result = asyncio.run(
        meal_service.upsert_meal(
            "user-1",
            {
                "cloudId": "meal-1",
                "mealId": "meal-1",
                "timestamp": "2026-03-03T12:00:00.000Z",
                "dayKey": "2026-03-03",
                "type": "lunch",
                "ingredients": [],
                "createdAt": "2026-03-03T12:00:00.000Z",
                "updatedAt": "2026-03-03T12:30:00.000Z",
                "deleted": False,
            },
        )
    )

    assert result["updatedAt"] == "2026-03-03T13:00:00.000Z"
    meal_ref.set.assert_not_called()
    sync_streak.assert_not_called()


def test_mark_deleted_creates_tombstone_when_meal_is_missing(
    mocker: MockerFixture,
) -> None:
    client = mocker.Mock()
    users_collection = mocker.Mock()
    user_ref = mocker.Mock()
    meals_collection = mocker.Mock()
    meal_ref = mocker.Mock()

    client.collection.return_value = users_collection
    users_collection.document.return_value = user_ref
    user_ref.collection.return_value = meals_collection
    meals_collection.document.return_value = meal_ref
    meal_ref.get.return_value = _build_snapshot(mocker, exists=False)
    mocker.patch("app.services.meal_service.get_firestore", return_value=client)
    sync_streak = mocker.patch("app.services.meal_service.streak_service.sync_streak_from_meals")

    result = asyncio.run(
        meal_service.mark_deleted(
            "user-1",
            "meal-1",
            updated_at="2026-03-03T12:30:00.000Z",
        )
    )

    assert result == {
        "userUid": "user-1",
        "mealId": "meal-1",
        "timestamp": "2026-03-03T12:30:00.000Z",
        "dayKey": None,
        "type": "other",
        "name": None,
        "ingredients": [],
        "createdAt": "2026-03-03T12:30:00.000Z",
        "updatedAt": "2026-03-03T12:30:00.000Z",
        "syncState": "synced",
        "source": None,
        "imageId": None,
        "photoUrl": None,
        "notes": None,
        "tags": [],
        "deleted": True,
        "cloudId": "meal-1",
        "totals": {"protein": 0.0, "fat": 0.0, "carbs": 0.0, "kcal": 0.0},
    }
    meal_ref.set.assert_called_once_with(result, merge=True)
    sync_streak.assert_called_once_with("user-1", reference_day_key=None)


def test_upload_photo_returns_storage_download_url(mocker: MockerFixture) -> None:
    bucket = mocker.Mock()
    bucket.name = "demo.appspot.com"
    blob = mocker.Mock()
    bucket.blob.return_value = blob
    upload = mocker.Mock()
    upload.filename = "meal.jpg"
    upload.content_type = "image/jpeg"
    upload.file = mocker.Mock()
    mocker.patch("app.services.meal_service.get_storage_bucket", return_value=bucket)

    payload = asyncio.run(meal_service.upload_photo("user-1", upload))

    bucket.blob.assert_called_once()
    blob.upload_from_file.assert_called_once_with(
        upload.file,
        content_type="image/jpeg",
    )
    blob.patch.assert_called_once_with()
    upload.file.seek.assert_called_once_with(0)
    upload.file.close.assert_called_once_with()
    assert payload["imageId"]
    assert payload["photoUrl"].startswith(
        "https://firebasestorage.googleapis.com/v0/b/demo.appspot.com/o/meals%2Fuser-1%2F"
    )


def test_resolve_photo_uses_meal_document_photo_url(mocker: MockerFixture) -> None:
    client = mocker.Mock()
    users_collection = mocker.Mock()
    user_ref = mocker.Mock()
    meals_collection = mocker.Mock()
    meal_ref = mocker.Mock()

    client.collection.return_value = users_collection
    users_collection.document.return_value = user_ref
    user_ref.collection.return_value = meals_collection
    meals_collection.document.return_value = meal_ref
    meal_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={
            "cloudId": "meal-1",
            "mealId": "meal-1",
            "userUid": "user-1",
            "timestamp": "2026-03-03T12:00:00.000Z",
            "type": "lunch",
            "ingredients": [],
            "createdAt": "2026-03-03T12:00:00.000Z",
            "updatedAt": "2026-03-03T12:00:00.000Z",
            "imageId": "image-1",
            "photoUrl": "https://cdn/meal.jpg",
            "deleted": False,
            "totals": {"kcal": 200, "protein": 30, "carbs": 0, "fat": 5},
        },
    )
    mocker.patch("app.services.meal_service.get_firestore", return_value=client)

    payload = asyncio.run(
        meal_service.resolve_photo("user-1", meal_id="meal-1", image_id="image-1")
    )

    assert payload == {
        "mealId": "meal-1",
        "imageId": "image-1",
        "photoUrl": "https://cdn/meal.jpg",
    }
