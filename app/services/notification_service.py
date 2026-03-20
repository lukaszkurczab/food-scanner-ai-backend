"""Backend-owned user notification definitions and preferences."""

from typing import Any, cast
import logging

from firebase_admin.exceptions import FirebaseError
from google.api_core.exceptions import GoogleAPICallError, RetryError
from google.cloud import firestore

from app.core.exceptions import FirestoreServiceError
from app.core.firestore_constants import (
    NOTIFICATIONS_SUBCOLLECTION,
    PREFS_SUBCOLLECTION,
    USERS_COLLECTION,
)
from app.db.firebase import get_firestore

logger = logging.getLogger(__name__)
GLOBAL_PREFS_DOCUMENT = "global"
VALID_NOTIFICATION_TYPES = frozenset({"meal_reminder", "calorie_goal", "day_fill"})
VALID_MEAL_KINDS = frozenset({"breakfast", "lunch", "dinner", "snack"})
DEFAULT_DAYS = [1, 2, 3, 4, 5, 6, 0]


class NotificationValidationError(Exception):
    """Raised when the notification payload is invalid."""


class NotificationPrefsValidationError(Exception):
    """Raised when the notification preferences payload is invalid."""


def _notification_collection(user_id: str) -> firestore.CollectionReference:
    client: firestore.Client = get_firestore()
    return client.collection(USERS_COLLECTION).document(user_id).collection(
        NOTIFICATIONS_SUBCOLLECTION
    )


def _notification_document(user_id: str, notification_id: str) -> firestore.DocumentReference:
    return _notification_collection(user_id).document(notification_id)


def _prefs_document(user_id: str) -> firestore.DocumentReference:
    client: firestore.Client = get_firestore()
    return (
        client.collection(USERS_COLLECTION)
        .document(user_id)
        .collection(PREFS_SUBCOLLECTION)
        .document(GLOBAL_PREFS_DOCUMENT)
    )


def _normalize_time(raw: object) -> dict[str, int]:
    if not isinstance(raw, dict):
        raise NotificationValidationError("Invalid notification time.")

    hour = raw.get("hour")
    minute = raw.get("minute")
    if not isinstance(hour, int) or hour < 0 or hour > 23:
        raise NotificationValidationError("Invalid notification hour.")
    if not isinstance(minute, int) or minute < 0 or minute > 59:
        raise NotificationValidationError("Invalid notification minute.")
    return {"hour": hour, "minute": minute}


def _normalize_days(raw: object) -> list[int]:
    if not isinstance(raw, list):
        raise NotificationValidationError("Invalid notification days.")
    raw_days: list[object] = raw
    days = sorted(
        {
            int(day)
            for day in raw_days
            if isinstance(day, int) and 0 <= day <= 6
        }
    )
    if len(days) != len(raw_days):
        raise NotificationValidationError("Invalid notification days.")
    return days or list(DEFAULT_DAYS)


def _normalize_notification_payload(payload: dict[str, Any]) -> dict[str, Any]:
    notification_id = payload.get("id")
    notification_type = payload.get("type")
    name = payload.get("name")
    text = payload.get("text")
    enabled = payload.get("enabled")
    created_at = payload.get("createdAt")
    updated_at = payload.get("updatedAt")
    meal_kind = payload.get("mealKind")
    kcal_by_hour = payload.get("kcalByHour")

    if not isinstance(notification_id, str) or not notification_id.strip():
        raise NotificationValidationError("Invalid notification id.")
    if notification_type not in VALID_NOTIFICATION_TYPES:
        raise NotificationValidationError("Invalid notification type.")
    if not isinstance(name, str) or not name.strip():
        raise NotificationValidationError("Invalid notification name.")
    if text is not None and not isinstance(text, str):
        raise NotificationValidationError("Invalid notification text.")
    if not isinstance(enabled, bool):
        raise NotificationValidationError("Invalid notification enabled flag.")
    if not isinstance(created_at, int) or created_at < 0:
        raise NotificationValidationError("Invalid notification createdAt.")
    if not isinstance(updated_at, int) or updated_at < 0:
        raise NotificationValidationError("Invalid notification updatedAt.")
    if meal_kind is not None and meal_kind not in VALID_MEAL_KINDS:
        raise NotificationValidationError("Invalid notification meal kind.")
    if kcal_by_hour is not None and not isinstance(kcal_by_hour, (int, float)):
        raise NotificationValidationError("Invalid notification kcalByHour.")

    return {
        "id": notification_id.strip(),
        "type": cast(str, notification_type),
        "name": name.strip(),
        "text": text,
        "time": _normalize_time(payload.get("time")),
        "days": _normalize_days(payload.get("days")),
        "enabled": enabled,
        "createdAt": created_at,
        "updatedAt": updated_at,
        "mealKind": meal_kind,
        "kcalByHour": float(kcal_by_hour) if isinstance(kcal_by_hour, (int, float)) else None,
    }


def _parse_notification_snapshot(snapshot: firestore.DocumentSnapshot) -> dict[str, Any] | None:
    raw = snapshot.to_dict() or {}
    if not isinstance(raw, dict):
        return None
    payload = dict(raw)
    payload.setdefault("id", snapshot.id)
    try:
        normalized = _normalize_notification_payload(payload)
    except NotificationValidationError:
        return None
    normalized["id"] = snapshot.id
    return normalized


def _normalize_quiet_hours(raw: object) -> dict[str, int] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise NotificationPrefsValidationError("Invalid quiet hours.")
    start_hour = raw.get("startHour")
    end_hour = raw.get("endHour")
    if not isinstance(start_hour, int) or not 0 <= start_hour <= 23:
        raise NotificationPrefsValidationError("Invalid quiet hours.")
    if not isinstance(end_hour, int) or not 0 <= end_hour <= 23:
        raise NotificationPrefsValidationError("Invalid quiet hours.")
    return {"startHour": start_hour, "endHour": end_hour}


def _normalize_weekdays(raw: object) -> list[int] | None:
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise NotificationPrefsValidationError("Invalid weekdays.")
    raw_days: list[object] = raw
    days = sorted(
        {
            int(day)
            for day in raw_days
            if isinstance(day, int) and 0 <= day <= 6
        }
    )
    if len(days) != len(raw_days):
        raise NotificationPrefsValidationError("Invalid weekdays.")
    return days


def _normalize_notifications_prefs_payload(
    payload: dict[str, Any],
) -> dict[str, Any]:
    normalized: dict[str, Any] = {}

    if "smartRemindersEnabled" in payload:
        if not isinstance(payload["smartRemindersEnabled"], bool):
            raise NotificationPrefsValidationError("Invalid smartRemindersEnabled.")
        normalized["smartRemindersEnabled"] = payload["smartRemindersEnabled"]

    if "motivationEnabled" in payload:
        if not isinstance(payload["motivationEnabled"], bool):
            raise NotificationPrefsValidationError("Invalid motivationEnabled.")
        normalized["motivationEnabled"] = payload["motivationEnabled"]

    if "statsEnabled" in payload:
        if not isinstance(payload["statsEnabled"], bool):
            raise NotificationPrefsValidationError("Invalid statsEnabled.")
        normalized["statsEnabled"] = payload["statsEnabled"]

    if "weekdays0to6" in payload:
        normalized["weekdays0to6"] = _normalize_weekdays(payload.get("weekdays0to6"))

    if "daysAhead" in payload:
        days_ahead = payload["daysAhead"]
        if days_ahead is not None and (
            not isinstance(days_ahead, int) or days_ahead < 1 or days_ahead > 14
        ):
            raise NotificationPrefsValidationError("Invalid daysAhead.")
        normalized["daysAhead"] = days_ahead

    if "quietHours" in payload:
        normalized["quietHours"] = _normalize_quiet_hours(payload.get("quietHours"))

    return normalized


def _normalize_notifications_prefs_doc(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}

    notifications = raw.get("notifications")
    if not isinstance(notifications, dict):
        return {}

    normalized: dict[str, Any] = {}

    smart_reminders_enabled = notifications.get("smartRemindersEnabled")
    if isinstance(smart_reminders_enabled, bool):
        normalized["smartRemindersEnabled"] = smart_reminders_enabled

    motivation_enabled = notifications.get("motivationEnabled")
    if isinstance(motivation_enabled, bool):
        normalized["motivationEnabled"] = motivation_enabled

    stats_enabled = notifications.get("statsEnabled")
    if isinstance(stats_enabled, bool):
        normalized["statsEnabled"] = stats_enabled

    weekdays = notifications.get("weekdays0to6")
    if isinstance(weekdays, list):
        normalized_days = [day for day in weekdays if isinstance(day, int) and 0 <= day <= 6]
        normalized["weekdays0to6"] = sorted(set(normalized_days))

    days_ahead = notifications.get("daysAhead")
    if isinstance(days_ahead, int) and 1 <= days_ahead <= 14:
        normalized["daysAhead"] = days_ahead

    quiet_hours = notifications.get("quietHours")
    if isinstance(quiet_hours, dict):
        start_hour = quiet_hours.get("startHour")
        end_hour = quiet_hours.get("endHour")
        if (
            isinstance(start_hour, int)
            and 0 <= start_hour <= 23
            and isinstance(end_hour, int)
            and 0 <= end_hour <= 23
        ):
            normalized["quietHours"] = {"startHour": start_hour, "endHour": end_hour}

    return normalized


async def list_notifications(user_id: str) -> list[dict[str, Any]]:
    notifications_ref = _notification_collection(user_id)

    try:
        snapshots = list(notifications_ref.stream())
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception("Failed to list notifications.", extra={"user_id": user_id})
        raise FirestoreServiceError("Failed to list notifications.") from exc

    items = [
        item
        for snapshot in snapshots
        if (item := _parse_notification_snapshot(snapshot)) is not None
    ]
    items.sort(key=lambda item: (item["createdAt"], item["id"]))
    return items


async def upsert_notification(user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_notification_payload(payload)
    notification_ref = _notification_document(user_id, normalized["id"])

    try:
        notification_ref.set(normalized, merge=True)
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception("Failed to upsert notification.", extra={"user_id": user_id})
        raise FirestoreServiceError("Failed to upsert notification.") from exc

    return normalized


async def delete_notification(user_id: str, notification_id: str) -> None:
    normalized_id = str(notification_id or "").strip()
    if not normalized_id:
        raise NotificationValidationError("Invalid notification id.")

    try:
        _notification_document(user_id, normalized_id).delete()
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception("Failed to delete notification.", extra={"user_id": user_id})
        raise FirestoreServiceError("Failed to delete notification.") from exc


async def get_notification_prefs(user_id: str) -> dict[str, Any]:
    prefs_ref = _prefs_document(user_id)

    try:
        snapshot = prefs_ref.get()
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception("Failed to fetch notification prefs.", extra={"user_id": user_id})
        raise FirestoreServiceError("Failed to fetch notification prefs.") from exc

    return _normalize_notifications_prefs_doc(snapshot.to_dict() or {})


async def update_notification_prefs(user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_notifications_prefs_payload(payload)
    prefs_ref = _prefs_document(user_id)

    try:
        existing_snapshot = prefs_ref.get()
        existing = _normalize_notifications_prefs_doc(existing_snapshot.to_dict() or {})
        merged = dict(existing)
        merged.update(normalized)
        prefs_ref.set({"notifications": merged}, merge=True)
    except NotificationPrefsValidationError:
        raise
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception("Failed to update notification prefs.", extra={"user_id": user_id})
        raise FirestoreServiceError("Failed to update notification prefs.") from exc

    return merged
