"""Backend-owned storage and uploads for saved meals."""

from datetime import datetime, timezone
import logging
from typing import Any
from uuid import uuid4
from fastapi import UploadFile
from firebase_admin.exceptions import FirebaseError
from google.api_core.exceptions import GoogleAPICallError, RetryError
from google.cloud import firestore

from app.core.exceptions import FirestoreServiceError
from app.core.firestore_constants import MY_MEALS_SUBCOLLECTION, USERS_COLLECTION
from app.db.firebase import get_firestore
from app.services import meal_storage
from app.services.meal_service import coerce_iso8601, normalize_meal_payload

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


def _normalize_saved_meal_payload(
    user_id: str,
    payload: dict[str, Any],
    *,
    fallback_cloud_id: str | None = None,
    fallback_updated_at: str | None = None,
) -> dict[str, Any]:
    normalized = normalize_meal_payload(
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
    normalized_updated_at = coerce_iso8601(updated_at)

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
