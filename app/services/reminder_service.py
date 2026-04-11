from __future__ import annotations

import logging

from pydantic import ValidationError as PydanticValidationError

from app.core.datetime_utils import utc_now
from app.core.exceptions import (
    ReminderDecisionContractError,
    ReminderUnavailableError,
)
from app.schemas.nutrition_state import NutritionStateResponse
from app.schemas.reminders import ReminderDecision
from app.services.notification_service import get_notification_prefs
from app.services.nutrition_state_service import get_nutrition_state
from app.services.reminder_decision_store import record_send_decision_if_new
from app.services.reminder_engine.types import ReminderContextInput
from app.services.reminder_inputs import build_reminder_inputs
from app.services.reminder_rule_engine import evaluate_reminder_decision

logger = logging.getLogger(__name__)


async def get_reminder_decision(
    user_id: str,
    *,
    day_key: str | None = None,
    tz_offset_min: int | None = None,
) -> ReminderDecision:
    state = await get_nutrition_state(user_id, day_key=day_key)
    _ensure_required_foundations_available(state)

    raw_prefs = await get_notification_prefs(user_id)
    now_utc = utc_now()
    reminder_inputs = await build_reminder_inputs(
        user_id=user_id,
        state=state,
        raw_prefs=raw_prefs,
        now_utc=now_utc,
        tz_offset_min=tz_offset_min,
    )

    try:
        decision = evaluate_reminder_decision(
            state=state,
            preferences=reminder_inputs.preferences,
            activity=reminder_inputs.activity,
            context=ReminderContextInput(now_local=reminder_inputs.now_local),
        )
    except PydanticValidationError as exc:
        raise ReminderDecisionContractError(
            f"Rule engine produced an invalid decision: {exc}"
        ) from exc

    logger.info(
        "reminder.decision.computed",
        extra={
            "user_id": user_id,
            "day_key": state.dayKey,
            "decision": decision.decision,
            "kind": decision.kind,
            "reason_codes": decision.reasonCodes,
            "confidence": decision.confidence,
            "tz_offset_min": tz_offset_min,
            "store_degraded": reminder_inputs.store_degraded,
        },
    )

    if decision.decision == "send":
        if decision.kind is None or decision.scheduledAtUtc is None:
            raise ReminderDecisionContractError(
                "Rule engine returned send decision without kind/scheduledAtUtc."
            )
        await record_send_decision_if_new(
            user_id,
            state.dayKey,
            kind=decision.kind,
            scheduled_at_utc=decision.scheduledAtUtc,
        )

    return decision


def _ensure_required_foundations_available(state: NutritionStateResponse) -> None:
    if state.meta.componentStatus.habits != "ok" or not state.habits.available:
        raise ReminderUnavailableError(
            "Smart reminders require available habit signals."
        )
