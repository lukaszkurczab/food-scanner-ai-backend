import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import pytest
from google.api_core.exceptions import FailedPrecondition, GoogleAPICallError
from pytest_mock import MockerFixture

from app.core.exceptions import FirestoreServiceError
from app.services import habit_signal_service

COMPUTED_AT = datetime(2026, 3, 18, 12, 0, tzinfo=UTC)


class _FilterLike(Protocol):
    field_path: str
    op_string: str
    value: Any


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
    logged_at_local_min: int | None = None,
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
    if logged_at_local_min is not None:
        payload["loggedAtLocalMin"] = logged_at_local_min
    return payload


class _FakeQuery:
    def __init__(
        self,
        collection: "_FakeMealsCollection",
        filters: list[_FilterLike] | None = None,
    ) -> None:
        self._collection = collection
        self._filters = filters or []

    def where(self, *, filter) -> "_FakeQuery":
        return _FakeQuery(self._collection, [*self._filters, filter])

    def stream(self):
        call = [
            (flt.field_path, flt.op_string, flt.value)
            for flt in self._filters
        ]
        self._collection.calls.append(call)
        if self._collection.fail_indexed_queries and any(field == "deleted" for field, _, _ in call):
            if self._collection.lazy_index_failures:
                return _LazyFailureIterator()
            raise FailedPrecondition("The query requires an index.")
        return iter(self._collection.snapshots)


class _FakeMealsCollection:
    def __init__(
        self,
        snapshots: list[object],
        *,
        fail_indexed_queries: bool = False,
        lazy_index_failures: bool = False,
    ) -> None:
        self.snapshots = snapshots
        self.calls: list[list[tuple[str, str, Any]]] = []
        self.fail_indexed_queries = fail_indexed_queries
        self.lazy_index_failures = lazy_index_failures

    def where(self, *, filter) -> _FakeQuery:
        return _FakeQuery(self, [filter])


class _LazyFailureIterator:
    def __iter__(self):
        return self

    def __next__(self):
        raise FailedPrecondition("The query requires an index.")


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
    assert response.behavior.validLoggingDays7 == 2
    assert response.behavior.avgMealsPerLoggedDay14 == 1.0
    assert response.behavior.avgValidMealsPerValidLoggedDay14 == 1.0
    assert response.dataQuality.daysUsingTimestampDayFallback14 == 1


def test_day_grouping_falls_back_to_timestamp_when_day_key_is_invalid() -> None:
    response = habit_signal_service.compute_habit_signals(
        profile=None,
        meals=[
            _meal(
                meal_id="meal-1",
                day_key="bad-day",
                timestamp="2026-03-18T08:00:00Z",
                kcal=400,
                protein=20,
            ),
        ],
        computed_at=COMPUTED_AT,
    )

    assert response.behavior.loggingDays7 == 1
    assert response.behavior.validLoggingDays7 == 1
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


def test_deleted_meals_are_excluded_from_habit_signals() -> None:
    response = habit_signal_service.compute_habit_signals(
        profile=None,
        meals=[
            _meal(
                meal_id="meal-1",
                day_key="2026-03-18",
                timestamp="2026-03-18T08:00:00Z",
                kcal=600,
                protein=30,
            ),
            _meal(
                meal_id="meal-2",
                day_key="2026-03-17",
                timestamp="2026-03-17T08:00:00Z",
                kcal=700,
                protein=35,
                deleted=True,
            ),
        ],
        computed_at=COMPUTED_AT,
    )

    assert response.behavior.loggingDays7 == 1
    assert response.behavior.avgMealsPerLoggedDay14 == 1.0


def test_meals_older_than_consistency_window_are_excluded() -> None:
    response = habit_signal_service.compute_habit_signals(
        profile=None,
        meals=[
            _meal(
                meal_id="meal-1",
                day_key="2026-03-18",
                timestamp="2026-03-18T08:00:00Z",
                kcal=600,
                protein=30,
            ),
            _meal(
                meal_id="meal-2",
                day_key="2026-02-10",
                timestamp="2026-02-10T08:00:00Z",
                kcal=700,
                protein=35,
            ),
        ],
        computed_at=COMPUTED_AT,
    )

    assert response.behavior.loggingDays7 == 1
    assert response.behavior.loggingConsistency28 == 0.0357


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
    assert response.behavior.dayCoverage14.loggedDays == 2
    assert response.behavior.dayCoverage14.validLoggedDays == 0
    assert response.behavior.validLoggingDays7 == 0
    assert response.behavior.avgValidMealsPerValidLoggedDay14 == 0


def test_structurally_weak_meals_do_not_count_as_valid_logging_foundation() -> None:
    response = habit_signal_service.compute_habit_signals(
        profile={"calorieTarget": 2000, "macroTargets": {"proteinGrams": 100}},
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
                kcal=0,
                protein=0,
                ingredients=[],
            ),
            _meal(
                meal_id="meal-3",
                day_key="2026-03-16",
                timestamp="2026-03-16T08:00:00Z",
                kcal=650,
                protein=42,
            ),
        ],
        computed_at=COMPUTED_AT,
    )

    assert response.behavior.loggingDays7 == 3
    assert response.behavior.validLoggingDays7 == 1
    assert response.behavior.dayCoverage14.loggedDays == 3
    assert response.behavior.dayCoverage14.validLoggedDays == 1
    assert response.topRisk == "under_logging"
    assert response.coachPriority == "logging_foundation"


def test_meal_type_frequency_counts_day_level_occurrence_for_valid_meals() -> None:
    response = habit_signal_service.compute_habit_signals(
        profile=None,
        meals=[
            _meal(
                meal_id="meal-1",
                day_key="2026-03-18",
                timestamp="2026-03-18T08:00:00Z",
                meal_type="breakfast",
                kcal=300,
                protein=15,
            ),
            _meal(
                meal_id="meal-2",
                day_key="2026-03-18",
                timestamp="2026-03-18T13:00:00Z",
                meal_type="lunch",
                kcal=600,
                protein=35,
            ),
            _meal(
                meal_id="meal-3",
                day_key="2026-03-17",
                timestamp="2026-03-17T08:00:00Z",
                meal_type="breakfast",
                kcal=320,
                protein=18,
            ),
        ],
        computed_at=COMPUTED_AT,
    )

    assert response.behavior.mealTypeFrequency14.breakfast == 2
    assert response.behavior.mealTypeFrequency14.lunch == 1
    assert response.behavior.mealTypeFrequency14.dinner == 0


def test_timing_patterns_prefer_logged_at_local_min_when_present() -> None:
    response = habit_signal_service.compute_habit_signals(
        profile=None,
        meals=[
            _meal(
                meal_id="meal-1",
                day_key="2026-03-18",
                timestamp="2026-03-18T07:00:00Z",
                meal_type="breakfast",
                kcal=300,
                protein=15,
                logged_at_local_min=8 * 60,
            ),
            _meal(
                meal_id="meal-2",
                day_key="2026-03-18",
                timestamp="2026-03-18T12:00:00Z",
                meal_type="lunch",
                kcal=600,
                protein=30,
                logged_at_local_min=13 * 60,
            ),
            _meal(
                meal_id="meal-3",
                day_key="2026-03-17",
                timestamp="2026-03-17T18:00:00Z",
                meal_type="dinner",
                kcal=700,
                protein=40,
                logged_at_local_min=19 * 60,
            ),
        ],
        computed_at=COMPUTED_AT,
    )

    assert response.behavior.timingPatterns14.available is True
    assert response.behavior.timingPatterns14.breakfastMedianHour == 8.0
    assert response.behavior.timingPatterns14.lunchMedianHour == 13.0
    assert response.behavior.timingPatterns14.dinnerMedianHour == 19.0
    assert response.dataQuality.daysUsingTimestampTimingFallback14 == 0


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

    meals_collection = _FakeMealsCollection([meal_snapshot])
    user_ref = mocker.Mock()
    user_ref.get.return_value = user_snapshot
    user_ref.collection.return_value = meals_collection
    client = mocker.Mock()
    client.collection.return_value.document.return_value = user_ref

    mocker.patch("app.services.habit_signal_service.get_firestore", return_value=client)

    response = asyncio.run(
        habit_signal_service.get_habit_signals("user-1", computed_at=COMPUTED_AT)
    )

    assert response.behavior.loggingDays7 == 1
    assert response.behavior.kcalAdherence14 == 0.9
    assert len(meals_collection.calls) == 2
    assert meals_collection.calls[0][0] == ("deleted", "==", False)
    assert meals_collection.calls[0][1][0] == "dayKey"
    assert meals_collection.calls[1][0] == ("deleted", "==", False)
    assert meals_collection.calls[1][1][0] == "timestamp"


def test_get_habit_signals_falls_back_when_index_is_missing(
    mocker: MockerFixture,
) -> None:
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

    meals_collection = _FakeMealsCollection([meal_snapshot], fail_indexed_queries=True)
    user_ref = mocker.Mock()
    user_ref.get.return_value = user_snapshot
    user_ref.collection.return_value = meals_collection
    client = mocker.Mock()
    client.collection.return_value.document.return_value = user_ref

    mocker.patch("app.services.habit_signal_service.get_firestore", return_value=client)

    response = asyncio.run(
        habit_signal_service.get_habit_signals("user-1", computed_at=COMPUTED_AT)
    )

    assert response.behavior.loggingDays7 == 1
    assert len(meals_collection.calls) == 4
    assert meals_collection.calls[0][0] == ("deleted", "==", False)
    assert meals_collection.calls[1][0][0] == "dayKey"
    assert all(field != "deleted" for field, _, _ in meals_collection.calls[1])
    assert meals_collection.calls[2][0] == ("deleted", "==", False)
    assert meals_collection.calls[3][0][0] == "timestamp"
    assert all(field != "deleted" for field, _, _ in meals_collection.calls[3])


def test_get_habit_signals_falls_back_when_index_fails_during_iteration(
    mocker: MockerFixture,
) -> None:
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

    meals_collection = _FakeMealsCollection(
        [meal_snapshot],
        fail_indexed_queries=True,
        lazy_index_failures=True,
    )
    user_ref = mocker.Mock()
    user_ref.get.return_value = user_snapshot
    user_ref.collection.return_value = meals_collection
    client = mocker.Mock()
    client.collection.return_value.document.return_value = user_ref

    mocker.patch("app.services.habit_signal_service.get_firestore", return_value=client)

    response = asyncio.run(
        habit_signal_service.get_habit_signals("user-1", computed_at=COMPUTED_AT)
    )

    assert response.behavior.loggingDays7 == 1
    assert len(meals_collection.calls) == 4


def test_get_habit_signals_wraps_firestore_errors(mocker: MockerFixture) -> None:
    client = mocker.Mock()
    user_ref = mocker.Mock()
    client.collection.return_value.document.return_value = user_ref
    user_ref.get.side_effect = GoogleAPICallError("boom")

    mocker.patch("app.services.habit_signal_service.get_firestore", return_value=client)

    with pytest.raises(FirestoreServiceError):
        asyncio.run(habit_signal_service.get_habit_signals("user-1"))
