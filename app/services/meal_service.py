"""Backend-owned storage and pagination for meals."""

from datetime import datetime, timezone
import logging
from typing import Any, TypedDict, cast
from uuid import uuid4

try:
    from typing import NotRequired
except ImportError:  # pragma: no cover - Python < 3.11 compatibility
    from typing_extensions import NotRequired

from fastapi import UploadFile
from firebase_admin.exceptions import FirebaseError
from google.api_core.exceptions import GoogleAPICallError, RetryError
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from app.core.coercion import coerce_float, coerce_optional_str
from app.core.exceptions import FirestoreServiceError
from app.core.firestore_constants import MEALS_SUBCOLLECTION, USERS_COLLECTION
from app.db.firebase import (
    build_storage_download_url,
    get_firestore,
    get_storage_bucket,
    get_storage_bucket_name,
)
from app.services import meal_storage, streak_service

logger = logging.getLogger(__name__)

DOCUMENT_ID_FIELD = "__name__"

MEAL_TYPES = {"breakfast", "lunch", "dinner", "snack", "other"}
MEAL_SOURCES = {"ai", "manual", "saved"}
MEAL_INPUT_METHODS = {"manual", "photo", "barcode", "text", "saved", "quick_add"}


class MealPhotoPayload(TypedDict):
    imageId: str
    photoUrl: str
    mealId: NotRequired[str | None]


def _as_object_map(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    raw_map = cast(dict[object, object], value)
    result: dict[str, object] = {}
    for raw_key, raw_item in raw_map.items():
        if isinstance(raw_key, str):
            result[raw_key] = raw_item
    return result


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def coerce_iso8601(value: Any, *, fallback: str | None = None) -> str:
    candidate = str(value or fallback or "").strip()
    if not candidate:
        raise ValueError("Missing ISO timestamp")

    datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    return candidate


def _as_bool(value: Any) -> bool:
    return bool(value)


def _normalize_tags(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    tags = cast(list[object], value)
    return [tag.strip() for tag in tags if isinstance(tag, str) and tag.strip()]


def _normalize_ingredients(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []

    raw_items = cast(list[object], value)
    items: list[dict[str, Any]] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        raw_map = cast(dict[str, object], raw)

        item_id = coerce_optional_str(raw_map.get("id"))
        name = coerce_optional_str(raw_map.get("name"))
        if not item_id or not name:
            continue

        unit = coerce_optional_str(raw_map.get("unit"))
        items.append(
            {
                "id": item_id,
                "name": name,
                "amount": coerce_float(raw_map.get("amount")),
                "unit": unit if unit in {"g", "ml"} else None,
                "kcal": coerce_float(raw_map.get("kcal")),
                "protein": coerce_float(raw_map.get("protein")),
                "fat": coerce_float(raw_map.get("fat")),
                "carbs": coerce_float(raw_map.get("carbs")),
            }
        )

    return items


def _compute_totals(ingredients: list[dict[str, Any]]) -> dict[str, float]:
    totals = {"protein": 0.0, "fat": 0.0, "carbs": 0.0, "kcal": 0.0}
    for ingredient in ingredients:
        totals["protein"] += float(ingredient.get("protein") or 0)
        totals["fat"] += float(ingredient.get("fat") or 0)
        totals["carbs"] += float(ingredient.get("carbs") or 0)
        totals["kcal"] += float(ingredient.get("kcal") or 0)
    return totals


def _normalize_totals(value: Any, ingredients: list[dict[str, Any]]) -> dict[str, float]:
    value_map = _as_object_map(value)
    if value_map is None:
        return _compute_totals(ingredients)

    return {
        "protein": coerce_float(value_map.get("protein")),
        "fat": coerce_float(value_map.get("fat")),
        "carbs": coerce_float(value_map.get("carbs")),
        "kcal": coerce_float(value_map.get("kcal")),
    }




def _meals_collection(user_id: str) -> firestore.CollectionReference:
    client: firestore.Client = get_firestore()
    return client.collection(USERS_COLLECTION).document(user_id).collection(MEALS_SUBCOLLECTION)


def _meal_ref(user_id: str, meal_id: str) -> firestore.DocumentReference:
    return _meals_collection(user_id).document(meal_id)


def _read_or_create_storage_token(blob: Any) -> str:
    metadata = dict(blob.metadata or {})
    existing = str(metadata.get("firebaseStorageDownloadTokens") or "").strip()
    token = existing.split(",", 1)[0].strip() if existing else ""
    if token:
        return token

    token = str(uuid4())
    metadata["firebaseStorageDownloadTokens"] = token
    blob.metadata = metadata
    blob.patch()
    return token


def _normalize_type(value: Any) -> str:
    meal_type = coerce_optional_str(value) or "other"
    return meal_type if meal_type in MEAL_TYPES else "other"


def _normalize_source(value: Any) -> str | None:
    source = coerce_optional_str(value)
    return source if source in MEAL_SOURCES else None


def _normalize_input_method(value: Any) -> str | None:
    input_method = coerce_optional_str(value)
    return input_method if input_method in MEAL_INPUT_METHODS else None


def _normalize_logged_at_local_min(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        normalized = int(value)
        return normalized if 0 <= normalized <= 1439 else None
    if isinstance(value, str) and value.strip().isdigit():
        normalized = int(value.strip())
        return normalized if 0 <= normalized <= 1439 else None
    return None


def _normalize_tz_offset_min(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        normalized = int(value)
        return normalized if -840 <= normalized <= 840 else None
    if isinstance(value, str):
        candidate = value.strip()
        if candidate.startswith(("+", "-")):
            candidate = candidate[0] + "".join(ch for ch in candidate[1:] if ch.isdigit())
        if candidate.lstrip("+-").isdigit():
            normalized = int(candidate)
            return normalized if -840 <= normalized <= 840 else None
    return None


def _normalize_ai_meta(value: Any) -> dict[str, Any] | None:
    value_map = _as_object_map(value)
    if value_map is None:
        return None

    model = coerce_optional_str(value_map.get("model"))
    run_id = coerce_optional_str(value_map.get("runId"))
    confidence_raw = value_map.get("confidence")
    confidence = None
    if confidence_raw is not None:
        confidence = coerce_float(confidence_raw)

    warnings_raw = value_map.get("warnings")
    warnings = (
        [
            warning.strip()
            for warning in cast(list[object], warnings_raw)
            if isinstance(warning, str) and warning.strip()
        ]
        if isinstance(warnings_raw, list)
        else []
    )

    if model is None and run_id is None and confidence is None and not warnings:
        return None

    return {
        "model": model,
        "runId": run_id,
        "confidence": confidence,
        "warnings": warnings,
    }


def normalize_meal_payload(
    user_id: str,
    payload: dict[str, Any],
    *,
    fallback_cloud_id: str | None = None,
    fallback_updated_at: str | None = None,
    fallback_day_key: str | None = None,
) -> dict[str, Any]:
    now_iso = _now_iso()
    cloud_id = coerce_optional_str(payload.get("cloudId")) or fallback_cloud_id
    meal_id = coerce_optional_str(payload.get("mealId")) or cloud_id
    if not cloud_id or not meal_id:
        raise ValueError("Missing meal identifier")

    ingredients = _normalize_ingredients(payload.get("ingredients"))
    updated_at = coerce_iso8601(payload.get("updatedAt"), fallback=fallback_updated_at or now_iso)
    timestamp = coerce_iso8601(payload.get("timestamp"), fallback=updated_at)
    created_at = coerce_iso8601(payload.get("createdAt"), fallback=timestamp)
    day_key = coerce_optional_str(payload.get("dayKey")) or fallback_day_key
    deleted = _as_bool(payload.get("deleted"))

    return {
        "userUid": user_id,
        "mealId": meal_id,
        "timestamp": timestamp,
        "dayKey": day_key,
        "loggedAtLocalMin": _normalize_logged_at_local_min(payload.get("loggedAtLocalMin")),
        "tzOffsetMin": _normalize_tz_offset_min(payload.get("tzOffsetMin")),
        "type": _normalize_type(payload.get("type")),
        "name": coerce_optional_str(payload.get("name")),
        "ingredients": ingredients,
        "createdAt": created_at,
        "updatedAt": updated_at,
        "syncState": "synced",
        "source": _normalize_source(payload.get("source")),
        "inputMethod": _normalize_input_method(
            payload.get("inputMethod", payload.get("input_method"))
        ),
        "aiMeta": _normalize_ai_meta(payload.get("aiMeta", payload.get("ai_meta"))),
        "imageId": coerce_optional_str(payload.get("imageId")),
        "photoUrl": coerce_optional_str(payload.get("photoUrl")),
        "notes": coerce_optional_str(payload.get("notes")),
        "tags": _normalize_tags(payload.get("tags")),
        "deleted": deleted,
        "cloudId": cloud_id,
        "totals": _normalize_totals(payload.get("totals"), ingredients),
    }


def _normalize_meal_snapshot(
    user_id: str,
    snapshot: firestore.DocumentSnapshot,
) -> dict[str, Any]:
    data = dict(snapshot.to_dict() or {})
    return normalize_meal_payload(
        user_id,
        data,
        fallback_cloud_id=snapshot.id,
        fallback_updated_at=coerce_optional_str(data.get("updatedAt")) or _now_iso(),
        fallback_day_key=coerce_optional_str(data.get("dayKey")),
    )


def _apply_history_filters(
    query: firestore.Query,
    *,
    calories: tuple[float, float] | None = None,
    protein: tuple[float, float] | None = None,
    carbs: tuple[float, float] | None = None,
    fat: tuple[float, float] | None = None,
    timestamp_start: str | None = None,
    timestamp_end: str | None = None,
) -> firestore.Query:
    if calories is not None:
        query = query.where(filter=FieldFilter("totals.kcal", ">=", calories[0])).where(
            filter=FieldFilter("totals.kcal", "<=", calories[1])
        )
    if protein is not None:
        query = query.where(filter=FieldFilter("totals.protein", ">=", protein[0])).where(
            filter=FieldFilter("totals.protein", "<=", protein[1])
        )
    if carbs is not None:
        query = query.where(filter=FieldFilter("totals.carbs", ">=", carbs[0])).where(
            filter=FieldFilter("totals.carbs", "<=", carbs[1])
        )
    if fat is not None:
        query = query.where(filter=FieldFilter("totals.fat", ">=", fat[0])).where(
            filter=FieldFilter("totals.fat", "<=", fat[1])
        )
    if timestamp_start is not None:
        query = query.where(filter=FieldFilter("timestamp", ">=", timestamp_start))
    if timestamp_end is not None:
        query = query.where(filter=FieldFilter("timestamp", "<=", timestamp_end))
    return query


async def list_history(
    user_id: str,
    *,
    limit_count: int = 20,
    before_cursor: str | None = None,
    calories: tuple[float, float] | None = None,
    protein: tuple[float, float] | None = None,
    carbs: tuple[float, float] | None = None,
    fat: tuple[float, float] | None = None,
    timestamp_start: str | None = None,
    timestamp_end: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    meals_ref = _meals_collection(user_id)

    try:
        query = meals_ref.where(filter=FieldFilter("deleted", "==", False)).order_by(
            "timestamp",
            direction=firestore.Query.DESCENDING,
        ).order_by(
            DOCUMENT_ID_FIELD,
            direction=firestore.Query.DESCENDING,
        )
        query = _apply_history_filters(
            query,
            calories=calories,
            protein=protein,
            carbs=carbs,
            fat=fat,
            timestamp_start=timestamp_start,
            timestamp_end=timestamp_end,
        )
        parsed_cursor = meal_storage.parse_cursor(before_cursor)
        if parsed_cursor is not None:
            cursor_timestamp, cursor_document_id = parsed_cursor
            query = (
                query.start_after([cursor_timestamp, cursor_document_id])
                if cursor_document_id
                else query.where(filter=FieldFilter("timestamp", "<", cursor_timestamp))
            )
        snapshots = list(query.limit(limit_count).stream())
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception("Failed to list meals history.", extra={"user_id": user_id})
        raise FirestoreServiceError("Failed to list meals history.") from exc

    items = [_normalize_meal_snapshot(user_id, snapshot) for snapshot in snapshots]
    next_cursor = (
        meal_storage.build_cursor(items[-1]["timestamp"], items[-1]["cloudId"])
        if len(items) == limit_count
        else None
    )
    return items, next_cursor


async def list_changes(
    user_id: str,
    *,
    limit_count: int = 100,
    after_cursor: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    meals_ref = _meals_collection(user_id)
    return await meal_storage.list_changes_paginated(
        meals_ref,
        user_id,
        _normalize_meal_snapshot,
        limit_count=limit_count,
        after_cursor=after_cursor,
        error_message="Failed to list meal changes.",
    )


async def upsert_meal(user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    normalized_payload = normalize_meal_payload(user_id, payload)
    meal_ref = _meal_ref(user_id, normalized_payload["cloudId"])

    try:
        snapshot = meal_ref.get()
        if snapshot.exists:
            existing = _normalize_meal_snapshot(user_id, snapshot)
            if existing["updatedAt"] > normalized_payload["updatedAt"]:
                return existing
            if not normalized_payload.get("dayKey"):
                normalized_payload["dayKey"] = coerce_optional_str(existing.get("dayKey"))
        meal_ref.set(normalized_payload, merge=True)
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to upsert meal.",
            extra={"user_id": user_id, "meal_id": normalized_payload.get("cloudId")},
        )
        raise FirestoreServiceError("Failed to upsert meal.") from exc

    await streak_service.sync_streak_from_meals(
        user_id,
        reference_day_key=coerce_optional_str(normalized_payload.get("dayKey")),
    )

    return normalized_payload


async def mark_deleted(
    user_id: str,
    meal_id: str,
    *,
    updated_at: str,
) -> dict[str, Any]:
    meal_ref = _meal_ref(user_id, meal_id)
    normalized_updated_at = coerce_iso8601(updated_at)

    try:
        snapshot = meal_ref.get()
        existing = dict(snapshot.to_dict() or {}) if snapshot.exists else {}
        payload = normalize_meal_payload(
            user_id,
            {
                **existing,
                "mealId": existing.get("mealId") or meal_id,
                "cloudId": existing.get("cloudId") or meal_id,
                "timestamp": existing.get("timestamp") or normalized_updated_at,
                "dayKey": existing.get("dayKey"),
                "type": existing.get("type") or "other",
                "createdAt": existing.get("createdAt") or normalized_updated_at,
                "updatedAt": normalized_updated_at,
                "deleted": True,
            },
            fallback_cloud_id=meal_id,
            fallback_updated_at=normalized_updated_at,
            fallback_day_key=coerce_optional_str(existing.get("dayKey")),
        )
        if snapshot.exists:
            existing_normalized = _normalize_meal_snapshot(user_id, snapshot)
            if existing_normalized["updatedAt"] > payload["updatedAt"]:
                return existing_normalized
        meal_ref.set(payload, merge=True)
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to delete meal.",
            extra={"user_id": user_id, "meal_id": meal_id},
        )
        raise FirestoreServiceError("Failed to delete meal.") from exc

    await streak_service.sync_streak_from_meals(
        user_id,
        reference_day_key=coerce_optional_str(payload.get("dayKey")),
    )

    return payload


async def upload_photo(user_id: str, upload: UploadFile) -> MealPhotoPayload:
    extension = "jpg"
    if upload.filename and "." in upload.filename:
        maybe_extension = upload.filename.rsplit(".", 1)[-1].strip().lower()
        if maybe_extension:
            extension = maybe_extension

    image_id = str(uuid4())
    return cast(
        MealPhotoPayload,
        await meal_storage.upload_photo_to_storage(
            user_id,
            upload,
            object_path=f"meals/{user_id}/{image_id}.{extension}",
            error_message="Failed to upload meal photo.",
        ),
    )

async def resolve_photo(
    user_id: str,
    *,
    meal_id: str | None = None,
    image_id: str | None = None,
) -> MealPhotoPayload:
    normalized_meal_id = coerce_optional_str(meal_id)
    normalized_image_id = coerce_optional_str(image_id)

    if not normalized_meal_id and not normalized_image_id:
        raise ValueError("Missing meal photo identifier")

    resolved_photo_url: str | None = None
    resolved_image_id = normalized_image_id

    if normalized_meal_id:
        meal_ref = _meal_ref(user_id, normalized_meal_id)
        try:
            snapshot = meal_ref.get()
        except (FirebaseError, GoogleAPICallError, RetryError) as exc:
            logger.exception(
                "Failed to load meal photo metadata.",
                extra={"user_id": user_id, "meal_id": normalized_meal_id},
            )
            raise FirestoreServiceError("Failed to load meal photo metadata.") from exc

        if snapshot.exists:
            normalized_meal = _normalize_meal_snapshot(user_id, snapshot)
            resolved_photo_url = coerce_optional_str(normalized_meal.get("photoUrl"))
            resolved_image_id = coerce_optional_str(normalized_meal.get("imageId")) or resolved_image_id

    if resolved_photo_url and resolved_image_id:
        return {
            "mealId": normalized_meal_id or None,
            "imageId": resolved_image_id,
            "photoUrl": resolved_photo_url,
        }

    if not resolved_image_id:
        raise ValueError("Meal photo not found")

    bucket = get_storage_bucket()
    candidate_paths = [
        f"meals/{user_id}/{resolved_image_id}.jpg",
        f"images/{resolved_image_id}.jpg",
    ]

    for object_path in candidate_paths:
        blob = bucket.blob(object_path)
        try:
            if not blob.exists():
                continue
            blob.reload()
            token = _read_or_create_storage_token(blob)
        except (FirebaseError, GoogleAPICallError, RetryError, OSError) as exc:
            logger.exception(
                "Failed to resolve meal photo URL.",
                extra={"user_id": user_id, "meal_id": normalized_meal_id, "image_id": resolved_image_id},
            )
            raise FirestoreServiceError("Failed to resolve meal photo URL.") from exc

        return {
            "mealId": normalized_meal_id or None,
            "imageId": resolved_image_id,
            "photoUrl": build_storage_download_url(
                get_storage_bucket_name(bucket),
                object_path,
                token,
            ),
        }

    raise ValueError("Meal photo not found")
