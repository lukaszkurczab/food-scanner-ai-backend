"""Backend-owned storage and uploads for saved meals."""

from datetime import UTC, datetime
import logging
from typing import Any
from uuid import uuid4

from fastapi import UploadFile
from firebase_admin.exceptions import FirebaseError
from google.api_core.exceptions import GoogleAPICallError, RetryError
from google.cloud import firestore

from app.core.exceptions import FirestoreServiceError
from app.db.firebase import (
    build_storage_download_url,
    get_firestore,
    get_storage_bucket,
    get_storage_bucket_name,
)
from app.services.meal_service import (
    DOCUMENT_ID_FIELD,
    _build_cursor,
    _coerce_iso8601,
    _normalize_meal_payload,
    _parse_cursor,
)

logger = logging.getLogger(__name__)

USERS_COLLECTION = "users"
MY_MEALS_SUBCOLLECTION = "myMeals"


def _my_meals_collection(user_id: str) -> firestore.CollectionReference:
    client: firestore.Client = get_firestore()
    return client.collection(USERS_COLLECTION).document(user_id).collection(MY_MEALS_SUBCOLLECTION)


def _my_meal_ref(user_id: str, meal_id: str) -> firestore.DocumentReference:
    return _my_meals_collection(user_id).document(meal_id)


async def list_changes(
    user_id: str,
    *,
    limit_count: int = 100,
    after_cursor: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    meals_ref = _my_meals_collection(user_id)

    try:
        query = meals_ref.order_by("updatedAt", direction=firestore.Query.ASCENDING).order_by(
            DOCUMENT_ID_FIELD,
            direction=firestore.Query.ASCENDING,
        )
        parsed_cursor = _parse_cursor(after_cursor)
        if parsed_cursor is not None:
            updated_at, document_id = parsed_cursor
            query = (
                query.start_after([updated_at, document_id])
                if document_id
                else query.where("updatedAt", ">", updated_at)
            )
        snapshots = list(query.limit(limit_count).stream())
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception("Failed to list saved meal changes.", extra={"user_id": user_id})
        raise FirestoreServiceError("Failed to list saved meal changes.") from exc

    items = [_normalize_saved_meal_snapshot(user_id, snapshot) for snapshot in snapshots]
    next_cursor = (
        _build_cursor(items[-1]["updatedAt"], items[-1]["cloudId"])
        if len(items) == limit_count
        else None
    )
    return items, next_cursor


def _normalize_saved_meal_payload(
    user_id: str,
    payload: dict[str, Any],
    *,
    fallback_cloud_id: str | None = None,
    fallback_updated_at: str | None = None,
) -> dict[str, Any]:
    normalized = _normalize_meal_payload(
        user_id,
        payload,
        fallback_cloud_id=fallback_cloud_id,
        fallback_updated_at=fallback_updated_at,
    )
    normalized["source"] = "saved"
    normalized["mealId"] = normalized["cloudId"]
    return normalized


def _normalize_saved_meal_snapshot(
    user_id: str,
    snapshot: firestore.DocumentSnapshot,
) -> dict[str, Any]:
    data = dict(snapshot.to_dict() or {})
    return _normalize_saved_meal_payload(
        user_id,
        data,
        fallback_cloud_id=snapshot.id,
        fallback_updated_at=str(data.get("updatedAt") or datetime.now(UTC).isoformat()),
    )


async def upsert_saved_meal(user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    normalized_payload = _normalize_saved_meal_payload(user_id, payload)
    meal_ref = _my_meal_ref(user_id, normalized_payload["cloudId"])

    try:
        snapshot = meal_ref.get()
        if snapshot.exists:
            existing = _normalize_saved_meal_snapshot(user_id, snapshot)
            if existing["updatedAt"] > normalized_payload["updatedAt"]:
                return existing
        meal_ref.set(normalized_payload, merge=True)
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to upsert saved meal.",
            extra={"user_id": user_id, "meal_id": normalized_payload.get("cloudId")},
        )
        raise FirestoreServiceError("Failed to upsert saved meal.") from exc

    return normalized_payload


async def mark_deleted(
    user_id: str,
    meal_id: str,
    *,
    updated_at: str,
) -> dict[str, Any]:
    meal_ref = _my_meal_ref(user_id, meal_id)
    normalized_updated_at = _coerce_iso8601(updated_at)

    try:
        snapshot = meal_ref.get()
        existing = dict(snapshot.to_dict() or {}) if snapshot.exists else {}
        payload = _normalize_saved_meal_payload(
            user_id,
            {
                **existing,
                "mealId": meal_id,
                "cloudId": meal_id,
                "timestamp": existing.get("timestamp") or normalized_updated_at,
                "type": existing.get("type") or "other",
                "createdAt": existing.get("createdAt") or normalized_updated_at,
                "updatedAt": normalized_updated_at,
                "deleted": True,
            },
            fallback_cloud_id=meal_id,
            fallback_updated_at=normalized_updated_at,
        )
        if snapshot.exists:
            existing_normalized = _normalize_saved_meal_snapshot(user_id, snapshot)
            if existing_normalized["updatedAt"] > payload["updatedAt"]:
                return existing_normalized
        meal_ref.set(payload, merge=True)
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to delete saved meal.",
            extra={"user_id": user_id, "meal_id": meal_id},
        )
        raise FirestoreServiceError("Failed to delete saved meal.") from exc

    return payload


async def upload_photo(
    user_id: str,
    meal_id: str,
    upload: UploadFile,
) -> dict[str, str]:
    bucket = get_storage_bucket()
    extension = "jpg"
    if upload.filename and "." in upload.filename:
        maybe_extension = upload.filename.rsplit(".", 1)[-1].strip().lower()
        if maybe_extension:
            extension = maybe_extension

    image_id = str(uuid4())
    object_path = f"myMeals/{user_id}/{meal_id}-{image_id}.{extension}"
    token = str(uuid4())
    blob = bucket.blob(object_path)

    try:
        upload.file.seek(0)
        blob.metadata = {"firebaseStorageDownloadTokens": token}
        blob.upload_from_file(
            upload.file,
            content_type=upload.content_type or "image/jpeg",
        )
        blob.patch()
    except (FirebaseError, GoogleAPICallError, RetryError, OSError) as exc:
        logger.exception(
            "Failed to upload saved meal photo.",
            extra={"user_id": user_id, "meal_id": meal_id},
        )
        raise FirestoreServiceError("Failed to upload saved meal photo.") from exc
    finally:
        upload.file.close()

    return {
        "mealId": meal_id,
        "imageId": image_id,
        "photoUrl": build_storage_download_url(
            get_storage_bucket_name(bucket),
            object_path,
            token,
        ),
    }
