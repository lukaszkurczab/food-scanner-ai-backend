import asyncio
from io import BytesIO

from pytest_mock import MockerFixture

from app.services import my_meal_service


def _build_snapshot(
    mocker: MockerFixture,
    *,
    exists: bool,
    data: dict[str, object] | None = None,
):
    snapshot = mocker.Mock()
    snapshot.exists = exists
    snapshot.id = str((data or {}).get("cloudId") or (data or {}).get("mealId") or "saved-1")
    snapshot.to_dict.return_value = data or {}
    return snapshot


def test_upsert_saved_meal_keeps_newer_remote_document(mocker: MockerFixture) -> None:
    client = mocker.Mock()
    users_collection = mocker.Mock()
    user_ref = mocker.Mock()
    my_meals_collection = mocker.Mock()
    meal_ref = mocker.Mock()

    client.collection.return_value = users_collection
    users_collection.document.return_value = user_ref
    user_ref.collection.return_value = my_meals_collection
    my_meals_collection.document.return_value = meal_ref
    meal_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={
            "cloudId": "saved-1",
            "mealId": "saved-1",
            "userUid": "user-1",
            "timestamp": "2026-03-03T12:00:00.000Z",
            "type": "lunch",
            "ingredients": [],
            "createdAt": "2026-03-03T12:00:00.000Z",
            "updatedAt": "2026-03-03T13:00:00.000Z",
            "source": "saved",
            "deleted": False,
            "totals": {"kcal": 200, "protein": 30, "carbs": 0, "fat": 5},
        },
    )
    mocker.patch("app.services.my_meal_service.get_firestore", return_value=client)

    result = asyncio.run(
        my_meal_service.upsert_saved_meal(
            "user-1",
            {
                "cloudId": "saved-1",
                "mealId": "saved-1",
                "timestamp": "2026-03-03T12:00:00.000Z",
                "type": "lunch",
                "ingredients": [],
                "createdAt": "2026-03-03T12:00:00.000Z",
                "updatedAt": "2026-03-03T12:30:00.000Z",
                "deleted": False,
            },
        ),
    )

    assert result["updatedAt"] == "2026-03-03T13:00:00.000Z"
    meal_ref.set.assert_not_called()


def test_upload_photo_returns_storage_download_url(mocker: MockerFixture) -> None:
    bucket = mocker.Mock()
    blob = mocker.Mock()
    bucket.name = "demo.appspot.com"
    bucket.blob.return_value = blob
    mocker.patch("app.services.my_meal_service.get_storage_bucket", return_value=bucket)

    upload = mocker.Mock()
    upload.filename = "saved.jpg"
    upload.content_type = "image/jpeg"
    upload.file = BytesIO(b"jpeg-bytes")

    payload = asyncio.run(my_meal_service.upload_photo("user-1", "saved-1", upload))

    bucket.blob.assert_called_once()
    blob.upload_from_file.assert_called_once()
    blob.patch.assert_called_once_with()
    assert payload["mealId"] == "saved-1"
    assert payload["imageId"]
    assert payload["photoUrl"].startswith(
        "https://firebasestorage.googleapis.com/v0/b/demo.appspot.com/o/"
    )
