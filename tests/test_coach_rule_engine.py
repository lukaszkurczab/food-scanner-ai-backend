import json
from pathlib import Path

from app.schemas.nutrition_state import NutritionStateResponse
from app.services.coach_rule_engine import (
    evaluate_coach_insights,
    select_top_insight,
)

FIXTURES_DIR = Path(__file__).parent / "contract_fixtures"


def _load_state_fixture() -> NutritionStateResponse:
    payload = json.loads((FIXTURES_DIR / "nutrition_state.json").read_text(encoding="utf-8"))
    return NutritionStateResponse.model_validate(payload)


def test_returns_no_insight_for_no_data_day() -> None:
    state = _load_state_fixture()
    state.quality.mealsLogged = 0

    evaluation = evaluate_coach_insights(state)

    assert evaluation.empty_reason == "no_data"
    assert evaluation.insights == []
    assert select_top_insight(evaluation.insights) is None


def test_returns_no_insight_for_insufficient_data() -> None:
    state = _load_state_fixture()
    state.quality.mealsLogged = 1
    state.quality.dataCompletenessScore = 0.4
    state.habits.behavior.validLoggingDays7 = 1
    state.habits.behavior.dayCoverage14.validLoggedDays = 1

    evaluation = evaluate_coach_insights(state)

    assert evaluation.empty_reason == "insufficient_data"
    assert evaluation.insights == []


def test_builds_under_logging_insight() -> None:
    state = _load_state_fixture()
    state.habits.topRisk = "under_logging"
    state.habits.behavior.validLoggingDays7 = 2
    state.habits.behavior.dayCoverage14.validLoggedDays = 5

    evaluation = evaluate_coach_insights(state)
    top_insight = select_top_insight(evaluation.insights)

    assert evaluation.empty_reason is None
    assert top_insight is not None
    assert top_insight.type == "under_logging"
    assert top_insight.actionType == "log_next_meal"
    assert "valid_logging_days_7_low" in top_insight.reasonCodes


def test_builds_unknown_meal_details_insight() -> None:
    state = _load_state_fixture()
    state.quality.missingNutritionMeals = 1
    state.habits.dataQuality.daysWithUnknownMealDetails14 = 4

    evaluation = evaluate_coach_insights(state)

    assert evaluation.insights[0].type == "high_unknown_meal_details"
    assert evaluation.insights[0].actionType == "review_history"
    assert evaluation.insights[0].reasonCodes == [
        "missing_nutrition_meals_today",
        "unknown_meal_details_14_high",
    ]


def test_builds_low_protein_consistency_insight() -> None:
    state = _load_state_fixture()
    state.habits.behavior.proteinDaysHit14.ratio = 0.35

    evaluation = evaluate_coach_insights(state)

    assert evaluation.insights[0].type == "low_protein_consistency"
    assert evaluation.insights[0].actionType == "open_chat"
    assert evaluation.insights[0].reasonCodes == ["protein_hit_ratio_14_low"]


def test_builds_calorie_under_target_insight() -> None:
    state = _load_state_fixture()
    state.habits.behavior.kcalUnderTargetRatio14 = 0.72

    evaluation = evaluate_coach_insights(state)

    assert evaluation.insights[0].type == "calorie_under_target"
    assert evaluation.insights[0].actionType == "log_next_meal"
    assert evaluation.insights[0].reasonCodes == ["kcal_under_target_ratio_14_high"]


def test_builds_positive_momentum_insight() -> None:
    state = _load_state_fixture()
    state.habits.behavior.validLoggingConsistency28 = 0.64
    state.streak.current = 4
    state.habits.behavior.kcalUnderTargetRatio14 = 0.2
    state.habits.behavior.proteinDaysHit14.ratio = 0.67
    state.quality.missingNutritionMeals = 0
    state.habits.dataQuality.daysWithUnknownMealDetails14 = 1
    state.habits.topRisk = "none"

    evaluation = evaluate_coach_insights(state)

    assert len(evaluation.insights) == 1
    assert evaluation.insights[0].type == "positive_momentum"
    assert evaluation.insights[0].isPositive is True
    assert evaluation.insights[0].reasonCodes == [
        "streak_positive",
        "consistency_improving",
    ]


def test_builds_stable_fallback_when_no_risk_and_no_positive_signal() -> None:
    state = _load_state_fixture()
    state.habits.behavior.validLoggingConsistency28 = 0.45
    state.streak.current = 1
    state.habits.behavior.kcalUnderTargetRatio14 = 0.2
    state.habits.behavior.proteinDaysHit14.ratio = 0.67
    state.quality.missingNutritionMeals = 0
    state.habits.dataQuality.daysWithUnknownMealDetails14 = 1
    state.habits.topRisk = "none"

    evaluation = evaluate_coach_insights(state)

    assert len(evaluation.insights) == 1
    assert evaluation.insights[0].type == "stable"
    assert evaluation.insights[0].actionType == "none"
    assert evaluation.insights[0].reasonCodes == [
        "streak_positive",
        "consistency_improving",
    ]


def test_select_top_insight_is_deterministic_and_respects_priority() -> None:
    state = _load_state_fixture()
    state.habits.topRisk = "under_logging"
    state.habits.behavior.validLoggingDays7 = 2
    state.quality.missingNutritionMeals = 1
    state.habits.dataQuality.daysWithUnknownMealDetails14 = 5
    state.habits.behavior.proteinDaysHit14.ratio = 0.31
    state.habits.behavior.kcalUnderTargetRatio14 = 0.77

    evaluation = evaluate_coach_insights(state)
    reversed_top = select_top_insight(list(reversed(evaluation.insights)))

    assert [insight.type for insight in evaluation.insights] == [
        "under_logging",
        "high_unknown_meal_details",
        "low_protein_consistency",
    ]
    assert len(evaluation.insights) == 3
    assert reversed_top is not None
    assert reversed_top.type == "under_logging"
