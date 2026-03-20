from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from app.core.datetime_utils import ensure_utc_datetime, parse_flexible_datetime
from app.services.meal_service import list_changes, list_history
from app.services.notification_service import list_notifications
from app.services.reminder_decision_store import get_daily_send_count
from app.schemas.nutrition_state import NutritionStateResponse
from app.services.reminder_rule_engine import (
    RECENT_ACTIVITY_SUPPRESSION_MIN,
    ReminderActivityInput,
    ReminderPreferencesInput,
    ReminderQuietHours,
    ReminderWindow,
)

PREFERRED_WINDOW_RADIUS_MIN = 60


@dataclass(frozen=True)
class ReminderInputs:
    preferences: ReminderPreferencesInput
    activity: ReminderActivityInput
    now_local: datetime


async def build_reminder_inputs(
    *,
    user_id: str,
    state: NutritionStateResponse,
    raw_prefs: dict[str, Any],
    now_utc: datetime,
) -> ReminderInputs:
    normalized_now = ensure_utc_datetime(now_utc)
    recent_meals = await _load_recent_meals(
        user_id=user_id,
        day_key=state.dayKey,
        now_utc=normalized_now,
    )
    recent_changes = await _load_recent_changes(
        user_id=user_id,
        now_utc=normalized_now,
    )
    latest_meal = await _load_latest_meal(user_id=user_id)
    now_local = _resolve_now_local(now_utc=normalized_now, latest_meal=latest_meal)
    notification_items = await _load_notification_items(user_id=user_id)
    daily_send_count = await get_daily_send_count(user_id, state.dayKey)

    return ReminderInputs(
        preferences=_build_preferences_input(
            raw_prefs=raw_prefs,
            notification_items=notification_items,
            now_local=now_local,
        ),
        activity=_build_activity_input(
            recent_meals=recent_meals,
            recent_changes=recent_changes,
            now_utc=normalized_now,
            daily_send_count=daily_send_count,
        ),
        now_local=now_local,
    )


async def _load_recent_meals(
    *,
    user_id: str,
    day_key: str,
    now_utc: datetime,
) -> list[dict[str, Any]]:
    del day_key
    timestamp_start = _serialize_utc_z(
        now_utc - timedelta(minutes=RECENT_ACTIVITY_SUPPRESSION_MIN)
    )
    timestamp_end = _serialize_utc_z(now_utc)
    meals, _ = await list_history(
        user_id,
        limit_count=5,
        timestamp_start=timestamp_start,
        timestamp_end=timestamp_end,
    )
    return meals


async def _load_recent_changes(
    *,
    user_id: str,
    now_utc: datetime,
) -> list[dict[str, Any]]:
    cutoff = _serialize_utc_z(now_utc - timedelta(minutes=RECENT_ACTIVITY_SUPPRESSION_MIN))
    changes, _ = await list_changes(
        user_id,
        limit_count=20,
        after_cursor=cutoff,
    )
    return changes


async def _load_latest_meal(*, user_id: str) -> dict[str, Any] | None:
    meals, _ = await list_history(
        user_id,
        limit_count=1,
    )
    if not meals:
        return None
    return meals[0]


async def _load_notification_items(*, user_id: str) -> list[dict[str, Any]]:
    return await list_notifications(user_id)


def _build_preferences_input(
    *,
    raw_prefs: dict[str, Any],
    notification_items: list[dict[str, Any]],
    now_local: datetime,
) -> ReminderPreferencesInput:
    reminders_enabled = _derive_reminders_enabled(
        raw_prefs=raw_prefs,
        notification_items=notification_items,
    )

    return ReminderPreferencesInput(
        reminders_enabled=reminders_enabled,
        quiet_hours=_build_quiet_hours(raw_prefs.get("quietHours")),
        first_meal_window=_derive_preferred_window(
            notification_items=notification_items,
            now_local=now_local,
            kind="log_first_meal",
        ),
        next_meal_window=_derive_preferred_window(
            notification_items=notification_items,
            now_local=now_local,
            kind="log_next_meal",
        ),
        complete_day_window=_derive_preferred_window(
            notification_items=notification_items,
            now_local=now_local,
            kind="complete_day",
        ),
    )


def _derive_reminders_enabled(
    *,
    raw_prefs: dict[str, Any],
    notification_items: list[dict[str, Any]],
) -> bool:
    explicit_value = raw_prefs.get("smartRemindersEnabled")
    if isinstance(explicit_value, bool):
        return explicit_value

    return any(_is_enabled_smart_reminder_preference(item) for item in notification_items)


def _build_quiet_hours(raw_quiet_hours: object) -> ReminderQuietHours | None:
    if not isinstance(raw_quiet_hours, dict):
        return None

    start_hour = raw_quiet_hours.get("startHour")
    end_hour = raw_quiet_hours.get("endHour")
    if not isinstance(start_hour, int) or not isinstance(end_hour, int):
        return None

    return ReminderQuietHours(start_hour=start_hour, end_hour=end_hour)


def _build_activity_input(
    *,
    recent_meals: list[dict[str, Any]],
    recent_changes: list[dict[str, Any]],
    now_utc: datetime,
    daily_send_count: int,
) -> ReminderActivityInput:
    return ReminderActivityInput(
        already_logged_recently=_already_logged_recently(
            recent_meals=recent_meals,
            now_utc=now_utc,
        ),
        recent_activity_detected=_derive_recent_activity_detected(
            recent_changes=recent_changes,
            now_utc=now_utc,
        ),
        daily_send_count=daily_send_count,
    )


def _already_logged_recently(
    *,
    recent_meals: list[dict[str, Any]],
    now_utc: datetime,
) -> bool:
    recent_cutoff = now_utc - timedelta(minutes=RECENT_ACTIVITY_SUPPRESSION_MIN)
    for meal in recent_meals:
        timestamp = parse_flexible_datetime(meal.get("timestamp"))
        if timestamp is None:
            continue
        if recent_cutoff <= timestamp <= now_utc:
            return True
    return False


def _derive_recent_activity_detected(
    *,
    recent_changes: list[dict[str, Any]],
    now_utc: datetime,
) -> bool:
    recent_cutoff = now_utc - timedelta(minutes=RECENT_ACTIVITY_SUPPRESSION_MIN)
    for change in recent_changes:
        updated_at = parse_flexible_datetime(change.get("updatedAt"))
        if updated_at is None or not (recent_cutoff <= updated_at <= now_utc):
            continue

        timestamp = parse_flexible_datetime(change.get("timestamp"))
        if timestamp is None:
            return True

        if timestamp < recent_cutoff or timestamp > now_utc:
            return True

    return False


def _derive_preferred_window(
    *,
    notification_items: list[dict[str, Any]],
    now_local: datetime,
    kind: str,
) -> ReminderWindow | None:
    current_min = now_local.hour * 60 + now_local.minute
    candidates = sorted(
        (
            window
            for item in notification_items
            if _matches_preferred_window_kind(item, kind=kind, now_local=now_local)
            if (window := _window_for_notification(item)) is not None
        ),
        key=lambda window: window.start_min,
    )

    active_candidates = [
        window
        for window in candidates
        if _is_minute_in_window(current_min, window)
    ]
    if active_candidates:
        return active_candidates[0]

    future_candidates = [
        window
        for window in candidates
        if current_min < window.start_min
    ]
    if future_candidates:
        return future_candidates[0]

    return None


def _is_enabled_smart_reminder_preference(notification_item: dict[str, Any]) -> bool:
    if not notification_item.get("enabled"):
        return False

    notification_type = notification_item.get("type")
    if notification_type == "day_fill":
        return True

    if notification_type != "meal_reminder":
        return False

    meal_kind = notification_item.get("mealKind")
    return meal_kind in {None, "breakfast", "lunch", "dinner", "snack"}


def _matches_preferred_window_kind(
    notification_item: dict[str, Any],
    *,
    kind: str,
    now_local: datetime,
) -> bool:
    if not notification_item.get("enabled"):
        return False

    days = notification_item.get("days")
    current_weekday_0_sun = (now_local.weekday() + 1) % 7
    if isinstance(days, list) and days and current_weekday_0_sun not in days:
        return False

    notification_type = notification_item.get("type")
    meal_kind = notification_item.get("mealKind")

    if kind == "complete_day":
        return notification_type == "day_fill"

    if notification_type != "meal_reminder":
        return False

    if kind == "log_first_meal":
        return meal_kind in {None, "breakfast"}

    return meal_kind in {None, "lunch", "dinner", "snack"}


def _window_for_notification(
    notification_item: dict[str, Any],
) -> ReminderWindow | None:
    time_value = notification_item.get("time")
    if not isinstance(time_value, dict):
        return None

    hour = time_value.get("hour")
    minute = time_value.get("minute")
    if not isinstance(hour, int) or not isinstance(minute, int):
        return None

    center_min = hour * 60 + minute
    if not 0 <= center_min <= 1439:
        return None

    return ReminderWindow(
        start_min=max(0, center_min - PREFERRED_WINDOW_RADIUS_MIN),
        end_min=min(1439, center_min + PREFERRED_WINDOW_RADIUS_MIN),
    )


def _is_minute_in_window(current_min: int, window: ReminderWindow) -> bool:
    if window.start_min == window.end_min:
        return True
    if window.start_min < window.end_min:
        return window.start_min <= current_min <= window.end_min
    return current_min >= window.start_min or current_min <= window.end_min


def _resolve_now_local(
    *,
    now_utc: datetime,
    latest_meal: dict[str, Any] | None,
) -> datetime:
    tz_offset_min = _derive_tz_offset_min(latest_meal)
    if tz_offset_min is None:
        return now_utc.astimezone(UTC)
    return now_utc.astimezone(timezone(timedelta(minutes=tz_offset_min)))


def _derive_tz_offset_min(latest_meal: dict[str, Any] | None) -> int | None:
    if not isinstance(latest_meal, dict):
        return None

    explicit_offset = latest_meal.get("tzOffsetMin")
    if isinstance(explicit_offset, int) and -840 <= explicit_offset <= 840:
        return explicit_offset
    if isinstance(explicit_offset, float):
        normalized_offset = int(explicit_offset)
        if -840 <= normalized_offset <= 840:
            return normalized_offset

    logged_at_local_min = latest_meal.get("loggedAtLocalMin")
    if not isinstance(logged_at_local_min, int):
        if isinstance(logged_at_local_min, float):
            logged_at_local_min = int(logged_at_local_min)
        else:
            return None
    if not 0 <= logged_at_local_min <= 1439:
        return None

    timestamp = parse_flexible_datetime(latest_meal.get("timestamp"))
    if timestamp is None:
        return None

    utc_minute = timestamp.hour * 60 + timestamp.minute
    offset_min = logged_at_local_min - utc_minute

    while offset_min > 840:
        offset_min -= 1440
    while offset_min < -840:
        offset_min += 1440

    if -840 <= offset_min <= 840:
        return offset_min
    return None


def _serialize_utc_z(value: datetime) -> str:
    return ensure_utc_datetime(value).isoformat().replace("+00:00", "Z")
