from __future__ import annotations

from datetime import datetime
from math import ceil

from app.schemas.nutrition_state import NutritionStateResponse
from app.schemas.reminders import ReminderDecision
from app.services.reminder_engine.timing import (
    _end_of_local_day,
    _evaluate_window_plan,
    _minute_of_day,
    _to_utc_z,
)
from app.services.reminder_engine.types import (
    COMPLETE_DAY_BUFFER_MIN,
    COMPLETE_DAY_WINDOW_RADIUS_MIN,
    FIRST_MEAL_WINDOW_RADIUS_MIN,
    LATEST_COMPLETE_DAY_START_MIN,
    NEXT_MEAL_WINDOW_RADIUS_MIN,
    ReminderPreferencesInput,
    ReminderTimingPolicy,
)


def evaluate_first_meal(
    state: NutritionStateResponse,
    preferences: ReminderPreferencesInput,
    now_local: datetime,
    computed_at: str,
    timing_policy: ReminderTimingPolicy,
) -> ReminderDecision:
    return _evaluate_first_meal_decision(
        state=state,
        preferences=preferences,
        now_local=now_local,
        current_min=_minute_of_day(now_local),
        computed_at=computed_at,
        timing_policy=timing_policy,
    )


def evaluate_next_meal(
    state: NutritionStateResponse,
    preferences: ReminderPreferencesInput,
    now_local: datetime,
    computed_at: str,
    timing_policy: ReminderTimingPolicy,
) -> ReminderDecision | None:
    return _evaluate_next_meal_decision(
        state=state,
        preferences=preferences,
        now_local=now_local,
        current_min=_minute_of_day(now_local),
        computed_at=computed_at,
        timing_policy=timing_policy,
    )


def evaluate_complete_day(
    state: NutritionStateResponse,
    preferences: ReminderPreferencesInput,
    now_local: datetime,
    computed_at: str,
    timing_policy: ReminderTimingPolicy,
) -> ReminderDecision | None:
    return _evaluate_complete_day_decision(
        state=state,
        preferences=preferences,
        now_local=now_local,
        current_min=_minute_of_day(now_local),
        computed_at=computed_at,
        timing_policy=timing_policy,
    )


def is_day_empty(state: NutritionStateResponse) -> bool:
    return _is_day_empty(state)


def is_later_incomplete_day(state: NutritionStateResponse, now_local: datetime) -> bool:
    return _is_later_incomplete_day(state, current_min=_minute_of_day(now_local))


def is_partially_logged_day(state: NutritionStateResponse) -> bool:
    return _is_partially_logged_day(state)


def is_day_already_complete(state: NutritionStateResponse) -> bool:
    return _is_day_already_complete(state)


def _evaluate_first_meal_decision(
    *,
    state: NutritionStateResponse,
    preferences: ReminderPreferencesInput,
    now_local: datetime,
    current_min: int,
    computed_at: str,
    timing_policy: ReminderTimingPolicy,
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
        observed_days=state.habits.behavior.timingPatterns14.observedDays,
        quiet_hours=preferences.quiet_hours,
        timing_policy=timing_policy,
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
    timing_policy: ReminderTimingPolicy,
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
        observed_days=state.habits.behavior.timingPatterns14.observedDays,
        quiet_hours=preferences.quiet_hours,
        timing_policy=timing_policy,
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
    timing_policy: ReminderTimingPolicy,
) -> ReminderDecision | None:
    if not _has_reasonable_complete_day_pattern(state):
        return None

    if state.quality.mealsLogged < _minimum_complete_day_meals(
        state,
        guarded=timing_policy.complete_day_guarded,
    ):
        return None

    window_evaluation = _evaluate_window_plan(
        now_local=now_local,
        current_min=current_min,
        preferred_window=preferences.complete_day_window,
        habit_minutes=_candidate_habit_minutes(state, "complete_day"),
        radius_min=COMPLETE_DAY_WINDOW_RADIUS_MIN,
        day_reason="day_partially_logged",
        observed_days=state.habits.behavior.timingPatterns14.observedDays,
        quiet_hours=preferences.quiet_hours,
        timing_policy=timing_policy,
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


def _is_day_empty(state: NutritionStateResponse) -> bool:
    return state.quality.mealsLogged <= 0


def _is_later_incomplete_day(state: NutritionStateResponse, *, current_min: int) -> bool:
    if not _is_partially_logged_day(state):
        return False

    later_start_min = max(
        LATEST_COMPLETE_DAY_START_MIN,
        _complete_day_anchor_min(state),
    )
    return current_min >= later_start_min


def _is_partially_logged_day(state: NutritionStateResponse) -> bool:
    return state.quality.mealsLogged > 0 and not _is_day_already_complete(state)


def _is_day_already_complete(state: NutritionStateResponse) -> bool:
    expected_meals = _expected_complete_meals(state)
    return (
        state.quality.mealsLogged >= expected_meals
        and state.quality.missingNutritionMeals == 0
        and state.quality.dataCompletenessScore >= 0.95
    )


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
        and timing.observedDays >= 4
        and timing.lastMealMedianHour is not None
    )


def _minimum_complete_day_meals(
    state: NutritionStateResponse,
    *,
    guarded: bool = False,
) -> int:
    expected_meals = _expected_complete_meals(state)
    if guarded:
        return expected_meals
    return max(1, expected_meals - 1)


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
    last_meal_min = _hour_to_minute(timing.lastMealMedianHour)
    if last_meal_min is None:
        return (None,)
    return (last_meal_min + COMPLETE_DAY_BUFFER_MIN,)


def _is_positive_momentum(state: NutritionStateResponse) -> bool:
    behavior = state.habits.behavior
    return behavior.validLoggingConsistency28 >= 0.5 and behavior.validLoggingDays7 >= 4


def _expected_complete_meals(state: NutritionStateResponse) -> int:
    habitual_average = state.habits.behavior.avgValidMealsPerValidLoggedDay14
    if habitual_average <= 0:
        return 3
    return max(3, ceil(habitual_average))


def _complete_day_anchor_min(state: NutritionStateResponse) -> int:
    timing = state.habits.behavior.timingPatterns14
    if timing.lastMealMedianHour is None:
        return LATEST_COMPLETE_DAY_START_MIN
    return max(
        LATEST_COMPLETE_DAY_START_MIN,
        int(timing.lastMealMedianHour * 60) + COMPLETE_DAY_BUFFER_MIN,
    )


def _hour_to_minute(value: float | None) -> int | None:
    if value is None:
        return None
    return int(value * 60)
