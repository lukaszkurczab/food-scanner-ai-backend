from __future__ import annotations

from pydantic import ValidationError as PydanticValidationError

from app.core.config import settings
from app.core.datetime_utils import utc_now
from app.core.exceptions import (
    ReminderDecisionContractError,
    ReminderUnavailableError,
    SmartRemindersDisabledError,
)
from app.schemas.nutrition_state import NutritionStateResponse
from app.schemas.reminders import ReminderDecision
from app.services.notification_service import get_notification_prefs
from app.services.nutrition_state_service import get_nutrition_state
from app.services.reminder_inputs import build_reminder_inputs
from app.services.reminder_rule_engine import ReminderContextInput, evaluate_reminder_decision


async def get_reminder_decision(
    user_id: str,
    *,
    day_key: str | None = None,
) -> ReminderDecision:
    if not settings.SMART_REMINDERS_ENABLED:
        raise SmartRemindersDisabledError("Smart reminders are disabled")

    state = await get_nutrition_state(user_id, day_key=day_key)
    _ensure_required_foundations_available(state)

    raw_prefs = await get_notification_prefs(user_id)
    now_utc = utc_now()
    reminder_inputs = await build_reminder_inputs(
        user_id=user_id,
        state=state,
        raw_prefs=raw_prefs,
        now_utc=now_utc,
    )

    try:
        return evaluate_reminder_decision(
            state=state,
            preferences=reminder_inputs.preferences,
            activity=reminder_inputs.activity,
            context=ReminderContextInput(now_local=reminder_inputs.now_local),
        )
    except PydanticValidationError as exc:
        raise ReminderDecisionContractError(
            f"Rule engine produced an invalid decision: {exc}"
        ) from exc


def _ensure_required_foundations_available(state: NutritionStateResponse) -> None:
    if state.meta.componentStatus.habits != "ok" or not state.habits.available:
        raise ReminderUnavailableError(
            "Smart reminders require available habit signals."
        )
