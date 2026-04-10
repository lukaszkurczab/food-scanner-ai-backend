from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
from typing import Callable, Sequence

from app.schemas.coach import (
    CoachActionType,
    CoachEmptyReason,
    CoachInsight,
    CoachInsightType,
)
from app.schemas.nutrition_state import NutritionStateResponse

UTC = timezone.utc

MAX_COACH_INSIGHTS = 3

INSIGHT_PRIORITY: dict[CoachInsightType, int] = {
    "under_logging": 100,
    "high_unknown_meal_details": 90,
    "low_protein_consistency": 80,
    "calorie_under_target": 70,
    "positive_momentum": 40,
    "stable": 10,
}


@dataclass(frozen=True)
class CoachRuleEvaluation:
    insights: list[CoachInsight]
    empty_reason: CoachEmptyReason | None = None


@dataclass(frozen=True)
class _RuleSpec:
    predicate: Callable[[NutritionStateResponse], bool]
    builder: Callable[[NutritionStateResponse], CoachInsight]


def evaluate_coach_insights(state: NutritionStateResponse) -> CoachRuleEvaluation:
    if state.quality.mealsLogged <= 0:
        return CoachRuleEvaluation(insights=[], empty_reason="no_data")

    if _has_insufficient_data(state):
        return CoachRuleEvaluation(insights=[], empty_reason="insufficient_data")

    risk_insights = [
        rule.builder(state)
        for rule in _RISK_RULES
        if rule.predicate(state)
    ]
    if risk_insights:
        ordered = sorted(risk_insights, key=_sort_key)
        return CoachRuleEvaluation(insights=ordered[:MAX_COACH_INSIGHTS])

    if _is_positive_momentum(state):
        return CoachRuleEvaluation(insights=[_build_positive_momentum_insight(state)])

    return CoachRuleEvaluation(insights=[_build_stable_insight(state)])


def select_top_insight(insights: Sequence[CoachInsight]) -> CoachInsight | None:
    if not insights:
        return None
    return sorted(insights, key=_sort_key)[0]


def _sort_key(insight: CoachInsight) -> tuple[int, int, str]:
    return (-insight.priority, _insight_rank(insight.type), insight.id)


def _insight_rank(insight_type: CoachInsightType) -> int:
    ordered: tuple[CoachInsightType, ...] = (
        "under_logging",
        "high_unknown_meal_details",
        "low_protein_consistency",
        "calorie_under_target",
        "positive_momentum",
        "stable",
    )
    return ordered.index(insight_type)


def _has_insufficient_data(state: NutritionStateResponse) -> bool:
    if not state.habits.available:
        return True

    behavior = state.habits.behavior
    quality = state.quality
    coverage = behavior.dayCoverage14.validLoggedDays

    if quality.dataCompletenessScore < 0.35:
        return True

    if behavior.validLoggingDays7 < 2 and coverage < 3:
        return True

    return coverage < 2


def _build_under_logging_insight(state: NutritionStateResponse) -> CoachInsight:
    reason_codes = _dedupe_reason_codes(
        [
            "valid_logging_days_7_low"
            if state.habits.behavior.validLoggingDays7 <= 2
            else None,
            "missing_nutrition_meals_today"
            if state.quality.missingNutritionMeals > 0
            else None,
            "meal_coverage_14_low"
            if state.habits.behavior.dayCoverage14.validLoggedDays < 4
            else None,
        ]
    )
    return _build_insight(
        state=state,
        insight_type="under_logging",
        title="Logging looks too light to coach well",
        body="Log your next meal so today is easier to interpret and adjust.",
        action_type="log_next_meal",
        action_label="Log next meal",
        reason_codes=reason_codes or ["valid_logging_days_7_low"],
        confidence=0.92,
        is_positive=False,
    )


def _build_high_unknown_meal_details_insight(
    state: NutritionStateResponse,
) -> CoachInsight:
    reason_codes = _dedupe_reason_codes(
        [
            "missing_nutrition_meals_today"
            if state.quality.missingNutritionMeals > 0
            else None,
            "unknown_meal_details_14_high"
            if state.habits.dataQuality.daysWithUnknownMealDetails14 >= 4
            else None,
        ]
    )
    return _build_insight(
        state=state,
        insight_type="high_unknown_meal_details",
        title="Some recent meals are too vague to trust",
        body="Review earlier logs and tighten meal details so trends stay reliable.",
        action_type="review_history",
        action_label="Review history",
        reason_codes=reason_codes or ["unknown_meal_details_14_high"],
        confidence=0.85,
        is_positive=False,
    )


def _build_low_protein_consistency_insight(
    state: NutritionStateResponse,
) -> CoachInsight:
    return _build_insight(
        state=state,
        insight_type="low_protein_consistency",
        title="Protein target is being missed too often",
        body="Open chat for one simple protein adjustment you can repeat this week.",
        action_type="open_chat",
        action_label="Open chat",
        reason_codes=["protein_hit_ratio_14_low"],
        confidence=0.82,
        is_positive=False,
    )


def _build_calorie_under_target_insight(state: NutritionStateResponse) -> CoachInsight:
    return _build_insight(
        state=state,
        insight_type="calorie_under_target",
        title="You are ending days under target too often",
        body="Log the next meal fully so intake stays closer to your target range.",
        action_type="log_next_meal",
        action_label="Log next meal",
        reason_codes=["kcal_under_target_ratio_14_high"],
        confidence=0.8,
        is_positive=False,
    )


def _build_positive_momentum_insight(state: NutritionStateResponse) -> CoachInsight:
    reason_codes = _dedupe_reason_codes(
        [
            "streak_positive" if state.streak.current >= 3 else None,
            "consistency_improving"
            if state.habits.behavior.validLoggingConsistency28 >= 0.6
            else None,
        ]
    )
    return _build_insight(
        state=state,
        insight_type="positive_momentum",
        title="Recent momentum is worth protecting",
        body="Keep the pattern going with one more complete log today.",
        action_type="open_chat",
        action_label="Open chat",
        reason_codes=reason_codes or ["streak_positive"],
        confidence=0.74,
        is_positive=True,
    )


def _build_stable_insight(state: NutritionStateResponse) -> CoachInsight:
    reason_codes = _dedupe_reason_codes(
        [
            "streak_positive" if state.streak.current > 0 else None,
            "consistency_improving"
            if state.habits.behavior.validLoggingConsistency28 >= 0.4
            else None,
        ]
    )
    return _build_insight(
        state=state,
        insight_type="stable",
        title="Your routine looks stable enough to maintain",
        body="Stay consistent today and use chat only if you want a deeper adjustment.",
        action_type="none",
        action_label=None,
        reason_codes=reason_codes or ["consistency_improving"],
        confidence=0.63,
        is_positive=False,
    )


def _build_insight(
    *,
    state: NutritionStateResponse,
    insight_type: CoachInsightType,
    title: str,
    body: str,
    action_type: CoachActionType,
    action_label: str | None,
    reason_codes: list[str],
    confidence: float,
    is_positive: bool,
) -> CoachInsight:
    return CoachInsight(
        id=f"{state.dayKey}:{insight_type}",
        type=insight_type,
        priority=INSIGHT_PRIORITY[insight_type],
        title=title,
        body=body,
        actionLabel=action_label,
        actionType=action_type,
        reasonCodes=reason_codes,
        source="rules",
        validUntil=_end_of_day_utc(state.dayKey),
        confidence=confidence,
        isPositive=is_positive,
    )


def _end_of_day_utc(day_key: str) -> str:
    day_value = datetime.strptime(day_key, "%Y-%m-%d").date()
    return (
        datetime.combine(day_value, time(23, 59, 59), tzinfo=UTC)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _dedupe_reason_codes(reason_codes: list[str | None]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for code in reason_codes:
        if code is None or code in seen:
            continue
        seen.add(code)
        ordered.append(code)
    return ordered


def _is_under_logging(state: NutritionStateResponse) -> bool:
    return (
        state.habits.topRisk == "under_logging"
        or state.habits.behavior.validLoggingDays7 <= 2
    )


def _is_high_unknown_meal_details(state: NutritionStateResponse) -> bool:
    return (
        state.quality.missingNutritionMeals > 0
        or state.habits.dataQuality.daysWithUnknownMealDetails14 >= 4
    )


def _is_low_protein_consistency(state: NutritionStateResponse) -> bool:
    ratio = state.habits.behavior.proteinDaysHit14.ratio
    return ratio is not None and ratio < 0.4


def _is_calorie_under_target(state: NutritionStateResponse) -> bool:
    ratio = state.habits.behavior.kcalUnderTargetRatio14
    return ratio is not None and ratio >= 0.6


def _is_positive_momentum(state: NutritionStateResponse) -> bool:
    return (
        state.habits.behavior.validLoggingConsistency28 >= 0.6
        or state.streak.current >= 3
    )


_RISK_RULES: tuple[_RuleSpec, ...] = (
    _RuleSpec(
        predicate=_is_under_logging,
        builder=_build_under_logging_insight,
    ),
    _RuleSpec(
        predicate=_is_high_unknown_meal_details,
        builder=_build_high_unknown_meal_details_insight,
    ),
    _RuleSpec(
        predicate=_is_low_protein_consistency,
        builder=_build_low_protein_consistency_insight,
    ),
    _RuleSpec(
        predicate=_is_calorie_under_target,
        builder=_build_calorie_under_target_insight,
    ),
)
