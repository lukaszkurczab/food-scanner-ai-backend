import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from google.api_core.exceptions import GoogleAPICallError
from pytest_mock import MockerFixture

from app.core.exceptions import FirestoreServiceError, HabitsDisabledError
from app.services import habit_signal_service

COMPUTED_AT = datetime(2026, 3, 18, 12, 0, tzinfo=UTC)


def _meal(
    *,
    meal_id: str,
    timestamp: str,
    day_key: str | None = None,
    meal_type: str = "lunch",
    kcal: float = 0,
    protein: float = 0,
    ingredients: list[dict[str, Any]] | None = None,
    ai_meta: dict[str, Any] | None = None,
    deleted: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "mealId": meal_id,
        "cloudId": meal_id,
        "timestamp": timestamp,
        "type": meal_type,
        "deleted": deleted,
        "totals": {"kcal": kcal, "protein": protein, "carbs": 0, "fat": 0},
        "ingredients": ingredients
        if ingredients is not None
        else [{"id": f"{meal_id}-i1", "name": "Ingredient", "amount": 100, "kcal": kcal, "protein": protein}],
    }
    if day_key is not None:
        payload["dayKey"] = day_key
    if ai_meta is not None:
        payload["aiMeta"] = ai_meta
    return payload


def test_day_grouping_prefers_day_key_over_timestamp() -> None:
    response = habit_signal_service.compute_habit_signals(
        profile=None,
        meals=[
            _meal(
                meal_id="meal-1",
                day_key="2026-03-17",
                timestamp="2026-03-16T23:30:00Z",
                kcal=400,
                protein=20,
            ),
            _meal(
                meal_id="meal-2",
                timestamp="2026-03-16T12:00:00Z",
                kcal=500,
                protein=30,
            ),
        ],
        computed_at=COMPUTED_AT,
    )

    assert response.behavior.loggingDays7 == 2
    assert response.behavior.avgMealsPerLoggedDay14 == 1.0


def test_logging_consistency_uses_28_day_window() -> None:
    meals = []
    for index in range(14):
        day = (COMPUTED_AT.date() - timedelta(days=index * 2)).isoformat()
        meals.append(
            _meal(
                meal_id=f"meal-{index}",
                day_key=day,
                timestamp=f"{day}T08:00:00Z",
                kcal=600,
                protein=30,
            )
        )

    response = habit_signal_service.compute_habit_signals(
        profile=None,
        meals=meals,
        computed_at=COMPUTED_AT,
    )

    assert response.behavior.loggingConsistency28 == 0.5


def test_kcal_adherence_and_under_target_ratio_use_valid_target() -> None:
    response = habit_signal_service.compute_habit_signals(
        profile={"calorieTarget": 2000},
        meals=[
            _meal(
                meal_id="meal-1",
                day_key="2026-03-18",
                timestamp="2026-03-18T08:00:00Z",
                kcal=1700,
                protein=80,
            ),
            _meal(
                meal_id="meal-2",
                day_key="2026-03-17",
                timestamp="2026-03-17T08:00:00Z",
                kcal=2100,
                protein=90,
            ),
        ],
        computed_at=COMPUTED_AT,
    )

    assert response.behavior.kcalAdherence14 == 0.95
    assert response.behavior.kcalUnderTargetRatio14 == 0.5


def test_protein_hit_logic_marks_days_unknown_when_target_is_missing() -> None:
    response = habit_signal_service.compute_habit_signals(
        profile={},
        meals=[
            _meal(
                meal_id="meal-1",
                day_key="2026-03-18",
                timestamp="2026-03-18T08:00:00Z",
                kcal=500,
                protein=95,
            ),
            _meal(
                meal_id="meal-2",
                day_key="2026-03-17",
                timestamp="2026-03-17T08:00:00Z",
                kcal=450,
                protein=80,
            ),
        ],
        computed_at=COMPUTED_AT,
    )

    assert response.behavior.proteinDaysHit14.hitDays == 0
    assert response.behavior.proteinDaysHit14.eligibleDays == 0
    assert response.behavior.proteinDaysHit14.unknownDays == 2
    assert response.behavior.proteinDaysHit14.ratio is None


def test_protein_hit_logic_uses_90_percent_of_target() -> None:
    response = habit_signal_service.compute_habit_signals(
        profile={"macroTargets": {"proteinGrams": 100}},
        meals=[
            _meal(
                meal_id="meal-1",
                day_key="2026-03-18",
                timestamp="2026-03-18T08:00:00Z",
                kcal=500,
                protein=95,
            ),
            _meal(
                meal_id="meal-2",
                day_key="2026-03-17",
                timestamp="2026-03-17T08:00:00Z",
                kcal=450,
                protein=80,
            ),
        ],
        computed_at=COMPUTED_AT,
    )

    assert response.behavior.proteinDaysHit14.hitDays == 1
    assert response.behavior.proteinDaysHit14.eligibleDays == 2
    assert response.behavior.proteinDaysHit14.unknownDays == 0
    assert response.behavior.proteinDaysHit14.ratio == 0.5


def test_unknown_meal_details_counts_low_detail_and_low_confidence_days() -> None:
    response = habit_signal_service.compute_habit_signals(
        profile=None,
        meals=[
            _meal(
                meal_id="meal-1",
                day_key="2026-03-18",
                timestamp="2026-03-18T08:00:00Z",
                kcal=0,
                protein=0,
                ingredients=[],
            ),
            _meal(
                meal_id="meal-2",
                day_key="2026-03-17",
                timestamp="2026-03-17T08:00:00Z",
                kcal=450,
                protein=30,
                ai_meta={"confidence": 0.2, "model": "gpt-5.4-mini"},
            ),
        ],
        computed_at=COMPUTED_AT,
    )

    assert response.dataQuality.daysWithUnknownMealDetails14 == 2


def test_top_risk_and_coach_priority_prioritize_under_logging() -> None:
    response = habit_signal_service.compute_habit_signals(
        profile={"calorieTarget": 2000, "macroTargets": {"proteinGrams": 100}},
        meals=[
            _meal(
                meal_id="meal-1",
                day_key="2026-03-18",
                timestamp="2026-03-18T08:00:00Z",
                kcal=500,
                protein=20,
            ),
            _meal(
                meal_id="meal-2",
                day_key="2026-03-14",
                timestamp="2026-03-14T08:00:00Z",
                kcal=400,
                protein=15,
            ),
        ],
        computed_at=COMPUTED_AT,
    )

    assert response.topRisk == "under_logging"
    assert response.coachPriority == "logging_foundation"


def test_top_risk_and_coach_priority_detect_low_protein_consistency() -> None:
    meals = [
        _meal(
            meal_id=f"meal-{index}",
            day_key=(COMPUTED_AT.date() - timedelta(days=index)).isoformat(),
            timestamp=f"{(COMPUTED_AT.date() - timedelta(days=index)).isoformat()}T08:00:00Z",
            kcal=2100,
            protein=40,
        )
        for index in range(20)
    ]

    response = habit_signal_service.compute_habit_signals(
        profile={"calorieTarget": 2000, "macroTargets": {"proteinGrams": 100}},
        meals=meals,
        computed_at=COMPUTED_AT,
    )

    assert response.topRisk == "low_protein_consistency"
    assert response.coachPriority == "protein_consistency"


def test_get_habit_signals_reads_firestore_and_returns_response(mocker: MockerFixture) -> None:
    user_snapshot = mocker.Mock()
    user_snapshot.exists = True
    user_snapshot.to_dict.return_value = {"calorieTarget": 2000}

    meal_snapshot = mocker.Mock()
    meal_snapshot.to_dict.return_value = _meal(
        meal_id="meal-1",
        day_key="2026-03-18",
        timestamp="2026-03-18T08:00:00Z",
        kcal=1800,
        protein=90,
    )

    meals_collection = mocker.Mock()
    meals_collection.stream.return_value = [meal_snapshot]
    user_ref = mocker.Mock()
    user_ref.get.return_value = user_snapshot
    user_ref.collection.return_value = meals_collection
    client = mocker.Mock()
    client.collection.return_value.document.return_value = user_ref

    mocker.patch("app.services.habit_signal_service.settings.HABITS_ENABLED", True)
    mocker.patch("app.services.habit_signal_service.get_firestore", return_value=client)

    response = asyncio.run(
        habit_signal_service.get_habit_signals("user-1", computed_at=COMPUTED_AT)
    )

    assert response.behavior.loggingDays7 == 1
    assert response.behavior.kcalAdherence14 == 0.9


def test_get_habit_signals_raises_when_feature_flag_is_disabled(
    mocker: MockerFixture,
) -> None:
    mocker.patch("app.services.habit_signal_service.settings.HABITS_ENABLED", False)

    with pytest.raises(HabitsDisabledError):
        asyncio.run(habit_signal_service.get_habit_signals("user-1"))


def test_get_habit_signals_wraps_firestore_errors(mocker: MockerFixture) -> None:
    client = mocker.Mock()
    user_ref = mocker.Mock()
    client.collection.return_value.document.return_value = user_ref
    user_ref.get.side_effect = GoogleAPICallError("boom")

    mocker.patch("app.services.habit_signal_service.settings.HABITS_ENABLED", True)
    mocker.patch("app.services.habit_signal_service.get_firestore", return_value=client)

    with pytest.raises(FirestoreServiceError):
        asyncio.run(habit_signal_service.get_habit_signals("user-1"))
