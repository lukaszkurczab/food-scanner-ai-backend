from __future__ import annotations

from app.core.exceptions import CoachUnavailableError
from app.schemas.coach import CoachMeta, CoachResponse
from app.schemas.nutrition_state import NutritionStateResponse
from app.services.coach_rule_engine import evaluate_coach_insights, select_top_insight
from app.services.nutrition_state_service import get_nutrition_state


async def get_coach_response(
    user_id: str,
    *,
    day_key: str | None = None,
) -> CoachResponse:
    state = await get_nutrition_state(user_id, day_key=day_key)
    _ensure_required_foundations_available(state)

    evaluation = evaluate_coach_insights(state)
    top_insight = select_top_insight(evaluation.insights)

    return CoachResponse(
        dayKey=state.dayKey,
        computedAt=state.computedAt,
        source="rules",
        insights=evaluation.insights,
        topInsight=top_insight,
        meta=CoachMeta(
            available=True,
            emptyReason=evaluation.empty_reason,
            isDegraded=_is_degraded_for_coach(state),
        ),
    )


def _ensure_required_foundations_available(state: NutritionStateResponse) -> None:
    if state.meta.componentStatus.habits != "ok" or not state.habits.available:
        raise CoachUnavailableError(
            "Coach insights require available habit signals."
        )


def _is_degraded_for_coach(state: NutritionStateResponse) -> bool:
    return state.meta.componentStatus.streak == "error"
