"""Backend-owned notification eligibility planning."""

from dataclasses import dataclass, replace
from datetime import datetime
import logging
from typing import Literal, cast

from firebase_admin.exceptions import FirebaseError
from google.api_core.exceptions import GoogleAPICallError, RetryError
from google.cloud import firestore

from app.core.exceptions import FirestoreServiceError
from app.db.firebase import get_firestore

logger = logging.getLogger(__name__)

USERS_COLLECTION = "users"
NOTIFICATIONS_SUBCOLLECTION = "notifications"
MEALS_SUBCOLLECTION = "meals"
VALID_AI_STYLES = {"none", "concise", "friendly", "detailed"}
VALID_NOTIFICATION_TYPES = {"meal_reminder", "calorie_goal", "day_fill"}
VALID_MEAL_KINDS = {"breakfast", "lunch", "dinner", "snack"}
DEFAULT_TIME = (12, 0)
DEFAULT_DAYS = [1, 2, 3, 4, 5, 6, 0]


@dataclass(frozen=True)
class NotificationTime:
    hour: int
    minute: int


@dataclass(frozen=True)
class NotificationPlan:
    id: str
    type: Literal["meal_reminder", "calorie_goal", "day_fill"]
    enabled: bool
    text: str | None
    time: NotificationTime
    days: list[int]
    meal_kind: Literal["breakfast", "lunch", "dinner", "snack"] | None
    kcal_by_hour: float | None
    should_schedule: bool
    missing_kcal: int | None = None


def _clamp_hour(value: object) -> int:
    if isinstance(value, (int, float)):
        return max(0, min(23, int(value)))
    return DEFAULT_TIME[0]


def _clamp_minute(value: object) -> int:
    if isinstance(value, (int, float)):
        return max(0, min(59, int(value)))
    return DEFAULT_TIME[1]


def _parse_days(value: object) -> list[int]:
    if not isinstance(value, list):
        return list(DEFAULT_DAYS)
    days = sorted(
        {
            int(day)
            for day in value
            if isinstance(day, (int, float)) and int(day) == day and 0 <= int(day) <= 6
        }
    )
    return days or list(DEFAULT_DAYS)


def _parse_ai_style(value: object) -> Literal["none", "concise", "friendly", "detailed"]:
    if value == "concise":
        return "concise"
    if value == "friendly":
        return "friendly"
    if value == "detailed":
        return "detailed"
    return "none"


def _parse_text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _parse_notification_doc(document_id: str, raw: object) -> NotificationPlan | None:
    if not isinstance(raw, dict):
        return None
    raw_map = cast(dict[str, object], raw)

    notif_type_raw = raw_map.get("type")
    if not isinstance(notif_type_raw, str) or notif_type_raw not in VALID_NOTIFICATION_TYPES:
        return None
    notif_type = cast(Literal["meal_reminder", "calorie_goal", "day_fill"], notif_type_raw)

    time_raw = raw_map.get("time")
    time_map = cast(dict[str, object], time_raw) if isinstance(time_raw, dict) else {}
    time = NotificationTime(
        hour=_clamp_hour(time_map.get("hour")),
        minute=_clamp_minute(time_map.get("minute")),
    )
    meal_kind = raw_map.get("mealKind")
    normalized_meal_kind = cast(
        Literal["breakfast", "lunch", "dinner", "snack"] | None,
        meal_kind if isinstance(meal_kind, str) and meal_kind in VALID_MEAL_KINDS else None,
    )
    kcal_by_hour_raw = raw_map.get("kcalByHour")

    return NotificationPlan(
        id=document_id,
        type=notif_type,
        enabled=bool(raw_map.get("enabled")),
        text=_parse_text(raw_map.get("text")),
        time=time,
        days=_parse_days(raw_map.get("days")),
        meal_kind=normalized_meal_kind,
        kcal_by_hour=float(kcal_by_hour_raw)
        if isinstance(kcal_by_hour_raw, (int, float))
        else None,
        should_schedule=False,
    )


def _sum_consumed_kcal(meals: list[dict[str, object]]) -> float:
    total = 0.0
    for meal in meals:
        totals = meal.get("totals")
        if isinstance(totals, dict):
            kcal = totals.get("kcal")
            if isinstance(kcal, (int, float)):
                total += float(kcal)
    return total


def _has_meal_type_today(meals: list[dict[str, object]], meal_kind: str) -> bool:
    return any(meal.get("type") == meal_kind for meal in meals)


def _is_kcal_below_threshold(consumed: float, threshold: float | None) -> bool:
    if threshold is None or threshold <= 0:
        return True
    return consumed < threshold


def _evaluate_notification_plan(
    notification: NotificationPlan,
    *,
    ai_style: str,
    target_kcal: float,
    meals: list[dict[str, object]],
) -> NotificationPlan:
    del ai_style
    if not notification.enabled:
        return notification

    if notification.type == "meal_reminder":
        if notification.meal_kind is None:
            return replace(notification, should_schedule=True)
        return replace(
            notification,
            should_schedule=not _has_meal_type_today(meals, notification.meal_kind),
        )

    if notification.type == "calorie_goal":
        consumed = _sum_consumed_kcal(meals)
        threshold = notification.kcal_by_hour
        if threshold is None:
            threshold = target_kcal
        should_schedule = _is_kcal_below_threshold(consumed, threshold)
        missing_kcal = max(0, round((threshold if threshold > 0 else 0) - consumed))
        return replace(
            notification,
            should_schedule=should_schedule,
            missing_kcal=missing_kcal,
        )

    return replace(notification, should_schedule=len(meals) == 0)


def _parse_target_kcal(raw_user: dict[str, object]) -> float:
    for key in ("calorieTarget", "targetKcal"):
        value = raw_user.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return 0.0


def _validate_iso_range(start_iso: str, end_iso: str) -> None:
    datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
    datetime.fromisoformat(end_iso.replace("Z", "+00:00"))


async def get_notification_plan(
    user_id: str,
    *,
    start_iso: str,
    end_iso: str,
) -> tuple[Literal["none", "concise", "friendly", "detailed"], list[NotificationPlan]]:
    _validate_iso_range(start_iso, end_iso)
    client: firestore.Client = get_firestore()
    user_ref = client.collection(USERS_COLLECTION).document(user_id)

    try:
        user_snapshot = user_ref.get()
        user_data = dict(user_snapshot.to_dict() or {}) if user_snapshot.exists else {}
        ai_style = _parse_ai_style(user_data.get("aiStyle"))
        target_kcal = _parse_target_kcal(user_data)

        notification_snapshots = list(user_ref.collection(NOTIFICATIONS_SUBCOLLECTION).stream())
        notifications = [
            parsed
            for snapshot in notification_snapshots
            if (
                parsed := _parse_notification_doc(
                    snapshot.id,
                    snapshot.to_dict() or {},
                )
            )
            is not None
        ]

        meals_query = (
            user_ref.collection(MEALS_SUBCOLLECTION)
            .where("timestamp", ">=", start_iso)
            .where("timestamp", "<=", end_iso)
        )
        meals = [
            dict(snapshot.to_dict() or {})
            for snapshot in meals_query.stream()
            if not bool((snapshot.to_dict() or {}).get("deleted"))
        ]
    except (ValueError, FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to build notification plan.",
            extra={"user_id": user_id},
        )
        raise FirestoreServiceError("Failed to build notification plan.") from exc

    plans = [
        _evaluate_notification_plan(
            notification,
            ai_style=ai_style,
            target_kcal=target_kcal,
            meals=meals,
        )
        for notification in notifications
    ]

    return ai_style, plans
