from __future__ import annotations

from datetime import datetime

from app.schemas.nutrition_state import NutritionStateResponse
from app.schemas.reminders import ReminderDecision, ReminderReasonCode
from app.services.reminder_engine.decisions import evaluate_complete_day, evaluate_first_meal, evaluate_next_meal, is_day_already_complete, is_day_empty, is_later_incomplete_day, is_partially_logged_day
from app.services.reminder_engine.profile import classify_profile, policy_for_profile
from app.services.reminder_engine.profile import _build_personalization_profile, _timing_policy_for_profile  # noqa: F401
from app.services.reminder_engine.suppression import evaluate_suppression
from app.services.reminder_engine.timing import _end_of_local_day, _normalize_local_datetime, _to_utc_z
from app.services.reminder_engine.types import (
    ReminderActivityInput,
    ReminderContextInput,
    ReminderPreferencesInput,
)


def evaluate_reminder_decision(
    *, state: NutritionStateResponse, preferences: ReminderPreferencesInput, activity: ReminderActivityInput, context: ReminderContextInput
) -> ReminderDecision:
    now_local = _normalize_local_datetime(context.now_local)
    computed_at = _to_utc_z(now_local)

    suppression = evaluate_suppression(preferences, activity, context)
    if suppression is not None:
        return suppression.model_copy(update={"dayKey": state.dayKey})

    if is_day_already_complete(state):
        return _noop(state, computed_at, ["day_already_complete"], 0.98, _end_of_local_day(now_local))

    policy = policy_for_profile(classify_profile(state))
    if is_day_empty(state):
        return evaluate_first_meal(state, preferences, now_local, computed_at, policy)

    if is_later_incomplete_day(state, now_local):
        decision = evaluate_complete_day(state, preferences, now_local, computed_at, policy)
        if decision is not None:
            return decision

    if is_partially_logged_day(state):
        decision = evaluate_next_meal(state, preferences, now_local, computed_at, policy)
        if decision is not None:
            return decision

    return _noop(state, computed_at, ["insufficient_signal"], 0.65, _end_of_local_day(now_local))


def _noop(
    state: NutritionStateResponse,
    computed_at: str,
    reason_codes: list[ReminderReasonCode],
    confidence: float,
    valid_until_local: datetime,
) -> ReminderDecision:
    return ReminderDecision(
        dayKey=state.dayKey,
        computedAt=computed_at,
        decision="noop",
        reasonCodes=reason_codes,
        confidence=confidence,
        validUntil=_to_utc_z(valid_until_local),
    )
