from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Iterable

from app.schemas.reminders import ReminderDecision, ReminderReasonCode
from app.services.reminder_engine.types import (
    DAILY_REMINDER_CAP,
    RECENT_ACTIVITY_SUPPRESSION_MIN,
    ReminderActivityInput,
    ReminderContextInput,
    ReminderPreferencesInput,
    ReminderQuietHours,
    UTC,
    REASON_CODE_ORDER,
)


def evaluate_suppression(
    preferences: ReminderPreferencesInput,
    activity: ReminderActivityInput,
    context: ReminderContextInput,
) -> ReminderDecision | None:
    """Returns a suppress ReminderDecision if any guard fires, else None."""
    now_local = _normalize_local_datetime(context.now_local)
    current_min = _minute_of_day(now_local)
    reason_codes = _collect_suppression_reason_codes(
        preferences=preferences,
        activity=activity,
        current_min=current_min,
    )
    if not reason_codes:
        return None

    return ReminderDecision(
        dayKey=now_local.date().isoformat(),
        computedAt=_to_utc_z(now_local),
        decision="suppress",
        reasonCodes=reason_codes,
        confidence=1.0,
        validUntil=_to_utc_z(
            _suppression_valid_until(
                now_local=now_local,
                reason_codes=reason_codes,
                quiet_hours=preferences.quiet_hours,
            )
        ),
    )


def _collect_suppression_reason_codes(
    *,
    preferences: ReminderPreferencesInput,
    activity: ReminderActivityInput,
    current_min: int,
) -> list[ReminderReasonCode]:
    reason_codes: list[ReminderReasonCode] = []

    if not preferences.reminders_enabled:
        reason_codes.append("reminders_disabled")
    if _is_within_quiet_hours(current_min, preferences.quiet_hours):
        reason_codes.append("quiet_hours")
    if activity.daily_send_count >= DAILY_REMINDER_CAP:
        reason_codes.append("frequency_cap_reached")
    if activity.already_logged_recently:
        reason_codes.append("already_logged_recently")
    if activity.recent_activity_detected:
        reason_codes.append("recent_activity_detected")

    return _ordered_reason_codes(reason_codes)


def _suppression_valid_until(
    *,
    now_local: datetime,
    reason_codes: list[ReminderReasonCode],
    quiet_hours: ReminderQuietHours | None,
) -> datetime:
    expiries: list[datetime] = []
    for reason_code in reason_codes:
        if reason_code == "quiet_hours":
            expiries.append(_quiet_hours_end(now_local, quiet_hours))
        elif reason_code in {"recent_activity_detected", "already_logged_recently"}:
            expiries.append(
                min(
                    now_local + timedelta(minutes=RECENT_ACTIVITY_SUPPRESSION_MIN),
                    _end_of_local_day(now_local),
                )
            )
        else:
            expiries.append(_end_of_local_day(now_local))
    return min(expiries)


def _is_within_quiet_hours(current_min: int, quiet_hours: ReminderQuietHours | None) -> bool:
    if quiet_hours is None:
        return False

    start_min = quiet_hours.start_hour * 60
    end_min = quiet_hours.end_hour * 60

    if start_min == end_min:
        return True
    if start_min < end_min:
        return start_min <= current_min < end_min
    return current_min >= start_min or current_min < end_min


def _quiet_hours_end(
    now_local: datetime,
    quiet_hours: ReminderQuietHours | None,
) -> datetime:
    if quiet_hours is None:
        return _end_of_local_day(now_local)

    end_time = time(quiet_hours.end_hour, 0, 0)
    current_min = _minute_of_day(now_local)
    end_min = quiet_hours.end_hour * 60
    same_day_end = datetime.combine(now_local.date(), end_time, tzinfo=now_local.tzinfo)

    if _is_within_quiet_hours(current_min, quiet_hours) and current_min < end_min:
        return same_day_end
    return same_day_end + timedelta(days=1)


def _ordered_reason_codes(reason_codes: Iterable[ReminderReasonCode]) -> list[ReminderReasonCode]:
    seen = set(reason_codes)
    return [code for code in REASON_CODE_ORDER if code in seen]


def _normalize_local_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value


def _minute_of_day(value: datetime) -> int:
    return value.hour * 60 + value.minute


def _to_utc_z(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _end_of_local_day(now_local: datetime) -> datetime:
    return datetime.combine(now_local.date(), time(23, 59, 59), tzinfo=now_local.tzinfo)
