"""Backend-owned storage and uploads for saved meals."""

from datetime import datetime, timezone
import logging
from typing import Any, cast
from uuid import uuid4
from fastapi import UploadFile
from firebase_admin.exceptions import FirebaseError
from google.api_core.exceptions import GoogleAPICallError, RetryError
from google.cloud import firestore

from app.core.exceptions import FirestoreServiceError
from app.core.firestore_constants import MY_MEALS_SUBCOLLECTION, USERS_COLLECTION
from app.db.firebase import get_firestore
from app.services import meal_storage
from app.services.meal_service import coerce_iso8601, normalize_meal_document_payload

logger = logging.getLogger(__name__)
UTC = timezone.utc


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
    return await meal_storage.list_changes_paginated(
        _my_meals_collection(user_id),
        user_id,
        _normalize_saved_meal_snapshot,
        limit_count=limit_count,
        after_cursor=after_cursor,
        error_message="Failed to list saved meal changes.",
    )


def _as_object_map(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    raw_map = cast(dict[object, object], value)
    result: dict[str, object] = {}
    for key, item in raw_map.items():
        if isinstance(key, str):
            result[key] = item
    return result


def _saved_meal_item_from_document(meal_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    image_ref_map = _as_object_map(payload.get("imageRef"))
    image_id = (
        str(value).strip()
        if image_ref_map and (value := image_ref_map.get("imageId")) is not None
        else None
    )
    photo_url = (
        str(value).strip()
        if image_ref_map and (value := image_ref_map.get("downloadUrl")) is not None
        else None
    )
    return {
        "id": meal_id,
        "loggedAt": payload.get("loggedAt"),
        "dayKey": payload.get("dayKey"),
        "loggedAtLocalMin": payload.get("loggedAtLocalMin"),
        "tzOffsetMin": payload.get("tzOffsetMin"),
        "type": payload.get("type"),
        "name": payload.get("name"),
        "ingredients": payload.get("ingredients"),
        "createdAt": payload.get("createdAt"),
        "updatedAt": payload.get("updatedAt"),
        "syncState": "synced",
        "source": "saved",
        "inputMethod": payload.get("inputMethod"),
        "aiMeta": payload.get("aiMeta"),
        "imageRef": image_ref_map,
        "notes": payload.get("notes"),
        "tags": payload.get("tags"),
        "deleted": bool(payload.get("deleted")),
        "totals": payload.get("totals"),
        # Legacy compatibility fields.
        "mealId": meal_id,
        "cloudId": meal_id,
        "timestamp": payload.get("loggedAt"),
        "imageId": image_id,
        "photoUrl": photo_url,
        "userUid": None,
    }


def _normalize_saved_meal_document(
    user_id: str,
    payload: dict[str, Any],
    *,
    fallback_cloud_id: str | None = None,
    fallback_updated_at: str | None = None,
) -> tuple[str, dict[str, Any]]:
    meal_id, normalized = normalize_meal_document_payload(
        user_id,
        payload,
        fallback_cloud_id=fallback_cloud_id,
        fallback_updated_at=fallback_updated_at,
    )
    normalized["source"] = "saved"
    return meal_id, normalized


def _normalize_saved_meal_snapshot(
    user_id: str,
    snapshot: firestore.DocumentSnapshot,
) -> dict[str, Any]:
    data: dict[str, Any] = dict(snapshot.to_dict() or {})
    meal_id, normalized = _normalize_saved_meal_document(
        user_id,
        data,
        fallback_cloud_id=snapshot.id,
        fallback_updated_at=str(data.get("updatedAt") or datetime.now(UTC).isoformat()),
    )
    return _saved_meal_item_from_document(meal_id, normalized)


async def upsert_saved_meal(user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    normalized_id, normalized_document = _normalize_saved_meal_document(user_id, payload)
    meal_ref = _my_meal_ref(user_id, normalized_id)

    try:
        snapshot = meal_ref.get()
        if snapshot.exists:
            existing = _normalize_saved_meal_snapshot(user_id, snapshot)
            if existing["updatedAt"] > normalized_document["updatedAt"]:
                return existing
        meal_ref.set(normalized_document, merge=True)
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to upsert saved meal.",
            extra={"user_id": user_id, "meal_id": normalized_id},
        )
        raise FirestoreServiceError("Failed to upsert saved meal.") from exc

    return _saved_meal_item_from_document(normalized_id, normalized_document)


async def mark_deleted(
    user_id: str,
    meal_id: str,
    *,
    updated_at: str,
) -> dict[str, Any]:
    meal_ref = _my_meal_ref(user_id, meal_id)
    normalized_updated_at = coerce_iso8601(updated_at)

    try:
        snapshot = meal_ref.get()
        existing: dict[str, Any] = dict(snapshot.to_dict() or {}) if snapshot.exists else {}
        normalized_id, normalized_document = _normalize_saved_meal_document(
            user_id,
            {
                **existing,
                "id": meal_id,
                "loggedAt": existing.get("loggedAt") or existing.get("timestamp") or normalized_updated_at,
                "type": existing.get("type") or "other",
                "createdAt": existing.get("createdAt")
                or existing.get("loggedAt")
                or existing.get("timestamp")
                or normalized_updated_at,
                "updatedAt": normalized_updated_at,
                "deleted": True,
            },
            fallback_cloud_id=meal_id,
            fallback_updated_at=normalized_updated_at,
        )
        if snapshot.exists:
            existing_normalized = _normalize_saved_meal_snapshot(user_id, snapshot)
            if existing_normalized["updatedAt"] > normalized_document["updatedAt"]:
                return existing_normalized
        meal_ref.set(normalized_document, merge=True)
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to delete saved meal.",
            extra={"user_id": user_id, "meal_id": meal_id},
        )
        raise FirestoreServiceError("Failed to delete saved meal.") from exc

    return _saved_meal_item_from_document(normalized_id, normalized_document)


async def upload_photo(
    user_id: str,
    meal_id: str,
    upload: UploadFile,
) -> dict[str, str]:
    extension = "jpg"
    if upload.filename and "." in upload.filename:
        maybe_extension = upload.filename.rsplit(".", 1)[-1].strip().lower()
        if maybe_extension:
            extension = maybe_extension

    payload = await meal_storage.upload_photo_to_storage(
        user_id,
        upload,
        object_path=f"myMeals/{user_id}/{meal_id}-{uuid4()}.{extension}",
        error_message="Failed to upload saved meal photo.",
    )
    return {
        "mealId": meal_id,
        "imageId": payload["imageId"],
        "photoUrl": payload["photoUrl"],
    }
