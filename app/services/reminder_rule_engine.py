from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from math import ceil
from typing import Iterable

from app.schemas.nutrition_state import NutritionStateResponse
from app.schemas.reminders import ReminderDecision, ReminderReasonCode

FIRST_MEAL_WINDOW_RADIUS_MIN = 90
NEXT_MEAL_WINDOW_RADIUS_MIN = 90
COMPLETE_DAY_WINDOW_RADIUS_MIN = 120
RECENT_ACTIVITY_SUPPRESSION_MIN = 90
LATEST_COMPLETE_DAY_START_MIN = 18 * 60

REASON_CODE_ORDER: tuple[ReminderReasonCode, ...] = (
    "reminders_disabled",
    "quiet_hours",
    "already_logged_recently",
    "recent_activity_detected",
    "preferred_window_open",
    "preferred_window_today",
    "habit_window_match",
    "habit_window_today",
    "day_empty",
    "day_partially_logged",
    "logging_usually_happens_now",
    "insufficient_signal",
    "day_already_complete",
)


@dataclass(frozen=True)
class ReminderWindow:
    start_min: int
    end_min: int


@dataclass(frozen=True)
class ReminderQuietHours:
    start_hour: int
    end_hour: int


@dataclass(frozen=True)
class ReminderPreferencesInput:
    reminders_enabled: bool = True
    quiet_hours: ReminderQuietHours | None = None
    first_meal_window: ReminderWindow | None = None
    next_meal_window: ReminderWindow | None = None
    complete_day_window: ReminderWindow | None = None


@dataclass(frozen=True)
class ReminderActivityInput:
    recent_activity_detected: bool = False
    already_logged_recently: bool = False


@dataclass(frozen=True)
class ReminderContextInput:
    now_local: datetime


@dataclass(frozen=True)
class _WindowEvaluation:
    reason_codes: list[ReminderReasonCode]
    scheduled_at_local: datetime
    valid_until_local: datetime


@dataclass(frozen=True)
class _ScheduledCandidate:
    scheduled_at_local: datetime
    valid_until_local: datetime
    reason_codes: list[ReminderReasonCode]


def evaluate_reminder_decision(
    *,
    state: NutritionStateResponse,
    preferences: ReminderPreferencesInput,
    activity: ReminderActivityInput,
    context: ReminderContextInput,
) -> ReminderDecision:
    now_local = _normalize_local_datetime(context.now_local)
    current_min = _minute_of_day(now_local)
    computed_at = _to_utc_z(now_local)

    suppressions = _collect_suppression_reason_codes(
        preferences=preferences,
        activity=activity,
        current_min=current_min,
    )
    if suppressions:
        return ReminderDecision(
            dayKey=state.dayKey,
            computedAt=computed_at,
            decision="suppress",
            reasonCodes=suppressions,
            confidence=1.0,
            validUntil=_to_utc_z(
                _suppression_valid_until(
                    now_local=now_local,
                    reason_codes=suppressions,
                    quiet_hours=preferences.quiet_hours,
                )
            ),
        )

    if _is_day_already_complete(state):
        return ReminderDecision(
            dayKey=state.dayKey,
            computedAt=computed_at,
            decision="noop",
            reasonCodes=["day_already_complete"],
            confidence=0.98,
            validUntil=_to_utc_z(_end_of_local_day(now_local)),
        )

    if _is_day_empty(state):
        return _evaluate_first_meal_decision(
            state=state,
            preferences=preferences,
            now_local=now_local,
            current_min=current_min,
            computed_at=computed_at,
        )

    if _is_later_incomplete_day(state, current_min=current_min):
        complete_day_decision = _evaluate_complete_day_decision(
            state=state,
            preferences=preferences,
            now_local=now_local,
            current_min=current_min,
            computed_at=computed_at,
        )
        if complete_day_decision is not None:
            return complete_day_decision

    if _is_partially_logged_day(state):
        next_meal_decision = _evaluate_next_meal_decision(
            state=state,
            preferences=preferences,
            now_local=now_local,
            current_min=current_min,
            computed_at=computed_at,
        )
        if next_meal_decision is not None:
            return next_meal_decision

    return ReminderDecision(
        dayKey=state.dayKey,
        computedAt=computed_at,
        decision="noop",
        reasonCodes=["insufficient_signal"],
        confidence=0.65,
        validUntil=_to_utc_z(_end_of_local_day(now_local)),
    )


def _evaluate_first_meal_decision(
    *,
    state: NutritionStateResponse,
    preferences: ReminderPreferencesInput,
    now_local: datetime,
    current_min: int,
    computed_at: str,
) -> ReminderDecision:
    if not _has_enough_signal(state):
        return ReminderDecision(
            dayKey=state.dayKey,
            computedAt=computed_at,
            decision="noop",
            reasonCodes=["insufficient_signal"],
            confidence=0.74,
            validUntil=_to_utc_z(_end_of_local_day(now_local)),
        )

    window_evaluation = _evaluate_window_plan(
        now_local=now_local,
        current_min=current_min,
        preferred_window=preferences.first_meal_window,
        habit_minutes=_candidate_habit_minutes(state, "log_first_meal"),
        radius_min=FIRST_MEAL_WINDOW_RADIUS_MIN,
        day_reason="day_empty",
    )
    if window_evaluation is None:
        return ReminderDecision(
            dayKey=state.dayKey,
            computedAt=computed_at,
            decision="noop",
            reasonCodes=["insufficient_signal"],
            confidence=0.7,
            validUntil=_to_utc_z(_end_of_local_day(now_local)),
        )

    return ReminderDecision(
        dayKey=state.dayKey,
        computedAt=computed_at,
        decision="send",
        kind="log_first_meal",
        reasonCodes=window_evaluation.reason_codes,
        scheduledAtUtc=_to_utc_z(window_evaluation.scheduled_at_local),
        confidence=0.87,
        validUntil=_to_utc_z(window_evaluation.valid_until_local),
    )


def _evaluate_next_meal_decision(
    *,
    state: NutritionStateResponse,
    preferences: ReminderPreferencesInput,
    now_local: datetime,
    current_min: int,
    computed_at: str,
) -> ReminderDecision | None:
    if not _has_enough_signal(state):
        return None

    window_evaluation = _evaluate_window_plan(
        now_local=now_local,
        current_min=current_min,
        preferred_window=preferences.next_meal_window,
        habit_minutes=_candidate_habit_minutes(state, "log_next_meal"),
        radius_min=NEXT_MEAL_WINDOW_RADIUS_MIN,
        day_reason="day_partially_logged",
    )
    if window_evaluation is None:
        return None

    return ReminderDecision(
        dayKey=state.dayKey,
        computedAt=computed_at,
        decision="send",
        kind="log_next_meal",
        reasonCodes=window_evaluation.reason_codes,
        scheduledAtUtc=_to_utc_z(window_evaluation.scheduled_at_local),
        confidence=0.84,
        validUntil=_to_utc_z(window_evaluation.valid_until_local),
    )


def _evaluate_complete_day_decision(
    *,
    state: NutritionStateResponse,
    preferences: ReminderPreferencesInput,
    now_local: datetime,
    current_min: int,
    computed_at: str,
) -> ReminderDecision | None:
    if not _has_reasonable_complete_day_pattern(state):
        return None

    window_evaluation = _evaluate_window_plan(
        now_local=now_local,
        current_min=current_min,
        preferred_window=preferences.complete_day_window,
        habit_minutes=_candidate_habit_minutes(state, "complete_day"),
        radius_min=COMPLETE_DAY_WINDOW_RADIUS_MIN,
        day_reason="day_partially_logged",
    )
    if window_evaluation is None:
        return None

    return ReminderDecision(
        dayKey=state.dayKey,
        computedAt=computed_at,
        decision="send",
        kind="complete_day",
        reasonCodes=window_evaluation.reason_codes,
        scheduledAtUtc=_to_utc_z(window_evaluation.scheduled_at_local),
        confidence=0.8,
        validUntil=_to_utc_z(window_evaluation.valid_until_local),
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


def _evaluate_window_plan(
    *,
    now_local: datetime,
    current_min: int,
    preferred_window: ReminderWindow | None,
    habit_minutes: Iterable[int | None],
    radius_min: int,
    day_reason: ReminderReasonCode,
) -> _WindowEvaluation | None:
    candidates: list[_ScheduledCandidate] = []

    preferred_candidate = _candidate_from_preferred_window(
        now_local=now_local,
        current_min=current_min,
        preferred_window=preferred_window,
        day_reason=day_reason,
    )
    if preferred_candidate is not None:
        candidates.append(preferred_candidate)

    for habit_min in habit_minutes:
        if habit_min is None:
            continue
        candidate = _candidate_from_habit_window(
            now_local=now_local,
            current_min=current_min,
            center_min=habit_min,
            radius_min=radius_min,
            day_reason=day_reason,
        )
        if candidate is not None:
            candidates.append(candidate)

    if not candidates:
        return None

    earliest_schedule = min(candidate.scheduled_at_local for candidate in candidates)
    active_candidates = [
        candidate
        for candidate in candidates
        if candidate.scheduled_at_local == earliest_schedule
    ]

    reason_codes: list[ReminderReasonCode] = []
    for candidate in active_candidates:
        reason_codes.extend(candidate.reason_codes)

    return _WindowEvaluation(
        reason_codes=_ordered_reason_codes(reason_codes),
        scheduled_at_local=earliest_schedule,
        valid_until_local=min(
            candidate.valid_until_local for candidate in active_candidates
        ),
    )


def _candidate_from_preferred_window(
    *,
    now_local: datetime,
    current_min: int,
    preferred_window: ReminderWindow | None,
    day_reason: ReminderReasonCode,
) -> _ScheduledCandidate | None:
    if preferred_window is None:
        return None

    window_end = _window_end_datetime(now_local, preferred_window)
    if now_local > window_end:
        return None

    if _is_minute_in_window(current_min, preferred_window):
        return _ScheduledCandidate(
            scheduled_at_local=now_local,
            valid_until_local=window_end,
            reason_codes=[day_reason, "preferred_window_open"],
        )

    window_start = _window_start_datetime(now_local, preferred_window)
    if now_local < window_start:
        return _ScheduledCandidate(
            scheduled_at_local=window_start,
            valid_until_local=window_end,
            reason_codes=[day_reason, "preferred_window_today"],
        )

    return None


def _candidate_from_habit_window(
    *,
    now_local: datetime,
    current_min: int,
    center_min: int,
    radius_min: int,
    day_reason: ReminderReasonCode,
) -> _ScheduledCandidate | None:
    window_end = _center_window_end_datetime(
        now_local=now_local,
        center_min=center_min,
        radius_min=radius_min,
    )
    if now_local > window_end:
        return None

    if _is_within_center_window(current_min, center_min, radius_min):
        return _ScheduledCandidate(
            scheduled_at_local=now_local,
            valid_until_local=window_end,
            reason_codes=[day_reason, "habit_window_match", "logging_usually_happens_now"],
        )

    window_start = _center_window_start_datetime(
        now_local=now_local,
        center_min=center_min,
        radius_min=radius_min,
    )
    if now_local < window_start:
        return _ScheduledCandidate(
            scheduled_at_local=window_start,
            valid_until_local=window_end,
            reason_codes=[day_reason, "habit_window_today"],
        )

    return None


def _has_enough_signal(state: NutritionStateResponse) -> bool:
    if not state.habits.available:
        return False

    timing = state.habits.behavior.timingPatterns14
    behavior = state.habits.behavior

    return (
        timing.available
        and timing.observedDays >= 3
        and behavior.validLoggingDays7 >= 2
        and behavior.dayCoverage14.validLoggedDays >= 3
        and state.quality.dataCompletenessScore >= 0.35
    )


def _has_reasonable_complete_day_pattern(state: NutritionStateResponse) -> bool:
    if not state.habits.available:
        return False

    timing = state.habits.behavior.timingPatterns14
    behavior = state.habits.behavior

    return (
        behavior.validLoggingConsistency28 >= 0.35
        and behavior.dayCoverage14.validLoggedDays >= 2
        and timing.available
        and timing.observedDays >= 2
        and timing.lastMealMedianHour is not None
    )


def _is_day_empty(state: NutritionStateResponse) -> bool:
    return state.quality.mealsLogged <= 0


def _is_day_already_complete(state: NutritionStateResponse) -> bool:
    expected_meals = _expected_complete_meals(state)
    return (
        state.quality.mealsLogged >= expected_meals
        and state.quality.missingNutritionMeals == 0
        and state.quality.dataCompletenessScore >= 0.95
    )


def _is_partially_logged_day(state: NutritionStateResponse) -> bool:
    return state.quality.mealsLogged > 0 and not _is_day_already_complete(state)


def _is_later_incomplete_day(state: NutritionStateResponse, *, current_min: int) -> bool:
    if not _is_partially_logged_day(state):
        return False

    later_start_min = max(
        LATEST_COMPLETE_DAY_START_MIN,
        _complete_day_anchor_min(state),
    )
    return current_min >= later_start_min


def _expected_complete_meals(state: NutritionStateResponse) -> int:
    habitual_average = state.habits.behavior.avgValidMealsPerValidLoggedDay14
    if habitual_average <= 0:
        return 3
    return max(3, ceil(habitual_average))


def _complete_day_anchor_min(state: NutritionStateResponse) -> int:
    timing = state.habits.behavior.timingPatterns14
    if timing.lastMealMedianHour is None:
        return LATEST_COMPLETE_DAY_START_MIN
    return max(LATEST_COMPLETE_DAY_START_MIN, int(timing.lastMealMedianHour * 60) - 60)


def _candidate_habit_minutes(
    state: NutritionStateResponse,
    kind: str,
) -> tuple[int | None, ...]:
    timing = state.habits.behavior.timingPatterns14
    if kind == "log_first_meal":
        return (_hour_to_minute(timing.firstMealMedianHour),)
    if kind == "log_next_meal":
        return (
            _hour_to_minute(timing.breakfastMedianHour),
            _hour_to_minute(timing.lunchMedianHour),
            _hour_to_minute(timing.dinnerMedianHour),
            _hour_to_minute(timing.snackMedianHour),
            _hour_to_minute(timing.lastMealMedianHour),
        )
    return (_hour_to_minute(timing.lastMealMedianHour),)


def _hour_to_minute(value: float | None) -> int | None:
    if value is None:
        return None
    return int(value * 60)


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
    """Serialize *value* to canonical ``YYYY-MM-DDTHH:MM:SSZ`` (exactly 20 chars).

    The explicit ``strftime`` avoids ``isoformat()`` emitting sub-second
    precision when the source datetime carries microseconds – which would
    violate the contract enforced by :class:`ReminderDecision`.
    """
    return value.astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _end_of_local_day(now_local: datetime) -> datetime:
    return datetime.combine(now_local.date(), time(23, 59, 59), tzinfo=now_local.tzinfo)


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


def _is_minute_in_window(current_min: int, window: ReminderWindow) -> bool:
    if window.start_min == window.end_min:
        return True
    if window.start_min < window.end_min:
        return window.start_min <= current_min <= window.end_min
    return current_min >= window.start_min or current_min <= window.end_min


def _window_end_datetime(now_local: datetime, window: ReminderWindow) -> datetime:
    end_hour, end_minute = divmod(window.end_min, 60)
    end_time = time(end_hour % 24, end_minute, 0)
    same_day_end = datetime.combine(now_local.date(), end_time, tzinfo=now_local.tzinfo)

    if window.start_min == window.end_min:
        return _end_of_local_day(now_local)
    if window.start_min < window.end_min:
        return same_day_end
    if _minute_of_day(now_local) <= window.end_min:
        return same_day_end
    return same_day_end + timedelta(days=1)


def _window_start_datetime(now_local: datetime, window: ReminderWindow) -> datetime:
    start_hour, start_minute = divmod(window.start_min, 60)
    return datetime.combine(
        now_local.date(),
        time(start_hour % 24, start_minute, 0),
        tzinfo=now_local.tzinfo,
    )


def _is_within_center_window(current_min: int, center_min: int, radius_min: int) -> bool:
    start_min = max(0, center_min - radius_min)
    end_min = min(1439, center_min + radius_min)
    return start_min <= current_min <= end_min


def _center_window_end_datetime(
    *,
    now_local: datetime,
    center_min: int,
    radius_min: int,
) -> datetime:
    end_min = min(1439, center_min + radius_min)
    end_hour, end_minute = divmod(end_min, 60)
    return datetime.combine(
        now_local.date(),
        time(end_hour, end_minute, 0),
        tzinfo=now_local.tzinfo,
    )


def _center_window_start_datetime(
    *,
    now_local: datetime,
    center_min: int,
    radius_min: int,
) -> datetime:
    start_min = max(0, center_min - radius_min)
    start_hour, start_minute = divmod(start_min, 60)
    return datetime.combine(
        now_local.date(),
        time(start_hour, start_minute, 0),
        tzinfo=now_local.tzinfo,
    )
