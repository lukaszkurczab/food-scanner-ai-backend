import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from google.api_core.exceptions import GoogleAPICallError
from pytest_mock import MockerFixture

from app.core.exceptions import FirestoreServiceError, StateDisabledError
from app.schemas.ai_credits import AiCreditsStatus, CreditCosts
from app.services import nutrition_state_service

NOW = datetime(2026, 3, 18, 9, 30, tzinfo=UTC)


def _meal(
    *,
    meal_id: str,
    timestamp: str,
    day_key: str | None = None,
    meal_type: str = "lunch",
    kcal: float = 0,
    protein: float = 0,
    carbs: float = 0,
    fat: float = 0,
    deleted: bool = False,
    ingredients: list[dict[str, Any]] | None = None,
    ai_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "mealId": meal_id,
        "cloudId": meal_id,
        "timestamp": timestamp,
        "type": meal_type,
        "deleted": deleted,
        "totals": {"kcal": kcal, "protein": protein, "carbs": carbs, "fat": fat},
        "ingredients": ingredients
        if ingredients is not None
        else [
            {
                "id": f"{meal_id}-1",
                "name": "Ingredient",
                "amount": 100,
                "kcal": kcal,
                "protein": protein,
                "carbs": carbs,
                "fat": fat,
            }
        ],
    }
    if day_key is not None:
        payload["dayKey"] = day_key
    if ai_meta is not None:
        payload["aiMeta"] = ai_meta
    return payload


def _credits_status() -> AiCreditsStatus:
    return AiCreditsStatus(
        userId="user-1",
        tier="premium",
        balance=640,
        allocation=800,
        periodStartAt=datetime(2026, 3, 1, tzinfo=UTC),
        periodEndAt=datetime(2026, 4, 1, tzinfo=UTC),
        costs=CreditCosts(chat=1, textMeal=1, photo=5),
        renewalAnchorSource="premium_cycle_start",
    )


# ---------------------------------------------------------------------------
# Fake Firestore helpers — supports bounded where().where().stream() chains
# ---------------------------------------------------------------------------


class _FakeQuery:
    """Chainable fake that records filter calls and returns snapshots."""

    def __init__(
        self,
        collection: "_FakeMealsCollection",
        filters: list[object] | None = None,
    ) -> None:
        self._collection = collection
        self._filters = filters or []

    def where(self, *, filter) -> "_FakeQuery":
        return _FakeQuery(self._collection, [*self._filters, filter])

    def stream(self):
        self._collection.calls.append(
            [
                (flt.field_path, flt.op_string, flt.value)
                for flt in self._filters
            ]
        )
        return self._collection.snapshots


class _FakeMealsCollection:
    """Fake meals collection that supports where().where().stream() chains."""

    def __init__(self, snapshots: list[object]) -> None:
        self.snapshots = snapshots
        self.calls: list[list[tuple[str, str, object]]] = []

    def where(self, *, filter) -> _FakeQuery:
        return _FakeQuery(self, [filter])


def _make_snapshot(mocker: MockerFixture, meal: dict[str, Any], doc_id: str | None = None) -> Any:
    snapshot = mocker.Mock()
    snapshot.id = doc_id or meal.get("mealId", "unknown")
    snapshot.to_dict.return_value = meal
    return snapshot


def _mock_firestore(
    mocker: MockerFixture,
    *,
    profile: dict[str, Any] | None,
    meals: list[dict[str, Any]],
    streak: dict[str, object] | None = None,
):
    """Build a full mock Firestore client supporting bounded queries."""
    user_snapshot = mocker.Mock()
    user_snapshot.exists = profile is not None
    user_snapshot.to_dict.return_value = profile or {}

    meal_snapshots = [_make_snapshot(mocker, m) for m in meals]
    meals_collection = _FakeMealsCollection(meal_snapshots)

    user_ref = mocker.Mock()
    user_ref.get.return_value = user_snapshot
    user_ref.collection.return_value = meals_collection

    client = mocker.Mock()
    client.collection.return_value.document.return_value = user_ref

    # Mock streak_service.get_streak used by build_streak_summary
    streak_data = streak if streak is not None else {"current": 0, "lastDate": None}
    mocker.patch(
        "app.services.nutrition_state_service.get_streak",
        return_value=streak_data,
    )

    return client, meals_collection


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_get_nutrition_state_happy_path(mocker: MockerFixture) -> None:
    profile = {
        "calorieTarget": 2000,
        "macroTargets": {"proteinGrams": 120, "carbsGrams": 220, "fatGrams": 70},
    }
    meals = [
        _meal(
            meal_id="breakfast-1",
            day_key="2026-03-18",
            timestamp="2026-03-18T08:00:00Z",
            meal_type="breakfast",
            kcal=500,
            protein=30,
            carbs=40,
            fat=10,
        ),
        _meal(
            meal_id="lunch-1",
            day_key="2026-03-18",
            timestamp="2026-03-18T13:00:00Z",
            meal_type="lunch",
            kcal=700,
            protein=45,
            carbs=60,
            fat=20,
        ),
        _meal(
            meal_id="history-1",
            day_key="2026-03-17",
            timestamp="2026-03-17T12:00:00Z",
            meal_type="dinner",
            kcal=1800,
            protein=90,
            carbs=100,
            fat=50,
        ),
    ]
    client, _ = _mock_firestore(
        mocker,
        profile=profile,
        meals=meals,
        streak={"current": 5, "lastDate": "2026-03-18"},
    )
    mocker.patch("app.services.nutrition_state_service.settings.STATE_ENABLED", True)
    mocker.patch("app.services.nutrition_state_service.settings.HABITS_ENABLED", True)
    mocker.patch("app.services.nutrition_state_service.get_firestore", return_value=client)
    mocker.patch(
        "app.services.nutrition_state_service.ai_credits_service.get_credits_status",
        return_value=_credits_status(),
    )

    response = asyncio.run(
        nutrition_state_service.get_nutrition_state(
            "user-1",
            day_key="2026-03-18",
            now=NOW,
        )
    )

    assert response.computedAt == "2026-03-18T09:30:00Z"
    assert response.dayKey == "2026-03-18"
    assert response.targets.model_dump() == {
        "kcal": 2000.0,
        "protein": 120.0,
        "carbs": 220.0,
        "fat": 70.0,
    }
    assert response.consumed.model_dump() == {
        "kcal": 1200.0,
        "protein": 75.0,
        "carbs": 100.0,
        "fat": 30.0,
    }
    assert response.remaining.model_dump() == {
        "kcal": 800.0,
        "protein": 45.0,
        "carbs": 120.0,
        "fat": 40.0,
    }
    assert response.quality.model_dump() == {
        "mealsLogged": 2,
        "missingNutritionMeals": 0,
        "dataCompletenessScore": 1.0,
    }
    assert response.habits.available is True
    assert response.streak.available is True
    assert response.streak.current == 5
    assert response.streak.lastDate == "2026-03-18"
    assert response.ai.available is True
    assert response.ai.usedThisPeriod == 160


# ---------------------------------------------------------------------------
# Empty day defaults
# ---------------------------------------------------------------------------


def test_get_nutrition_state_returns_empty_day_defaults(mocker: MockerFixture) -> None:
    client, _ = _mock_firestore(
        mocker,
        profile={"calorieTarget": 1800},
        meals=[
            _meal(
                meal_id="old-1",
                day_key="2026-03-17",
                timestamp="2026-03-17T12:00:00Z",
                kcal=500,
                protein=25,
            )
        ],
    )
    mocker.patch("app.services.nutrition_state_service.settings.STATE_ENABLED", True)
    mocker.patch("app.services.nutrition_state_service.get_firestore", return_value=client)
    mocker.patch(
        "app.services.nutrition_state_service.ai_credits_service.get_credits_status",
        return_value=_credits_status(),
    )

    response = asyncio.run(
        nutrition_state_service.get_nutrition_state(
            "user-1",
            day_key="2026-03-18",
            now=NOW,
        )
    )

    assert response.consumed.kcal == 0
    assert response.remaining.kcal == 1800
    assert response.quality.mealsLogged == 0
    assert response.quality.dataCompletenessScore == 0


# ---------------------------------------------------------------------------
# Missing targets
# ---------------------------------------------------------------------------


def test_get_nutrition_state_handles_missing_targets(mocker: MockerFixture) -> None:
    client, _ = _mock_firestore(
        mocker,
        profile={},
        meals=[
            _meal(
                meal_id="meal-1",
                day_key="2026-03-18",
                timestamp="2026-03-18T12:00:00Z",
                kcal=400,
                protein=20,
            )
        ],
    )
    mocker.patch("app.services.nutrition_state_service.settings.STATE_ENABLED", True)
    mocker.patch("app.services.nutrition_state_service.get_firestore", return_value=client)
    mocker.patch(
        "app.services.nutrition_state_service.ai_credits_service.get_credits_status",
        return_value=_credits_status(),
    )

    response = asyncio.run(
        nutrition_state_service.get_nutrition_state(
            "user-1",
            day_key="2026-03-18",
            now=NOW,
        )
    )

    assert response.targets.model_dump() == {
        "kcal": None,
        "protein": None,
        "carbs": None,
        "fat": None,
    }
    assert response.remaining.model_dump() == {
        "kcal": None,
        "protein": None,
        "carbs": None,
        "fat": None,
    }


# ---------------------------------------------------------------------------
# Graceful degradation
# ---------------------------------------------------------------------------


def test_get_nutrition_state_degrades_gracefully_for_subservices(
    mocker: MockerFixture,
) -> None:
    client, _ = _mock_firestore(
        mocker,
        profile={"calorieTarget": 1800},
        meals=[
            _meal(
                meal_id="meal-1",
                day_key="2026-03-18",
                timestamp="2026-03-18T12:00:00Z",
                kcal=400,
                protein=20,
            )
        ],
    )
    mocker.patch("app.services.nutrition_state_service.settings.STATE_ENABLED", True)
    mocker.patch("app.services.nutrition_state_service.get_firestore", return_value=client)
    mocker.patch(
        "app.services.nutrition_state_service.build_habits_summary",
        side_effect=RuntimeError("habits failed"),
    )
    mocker.patch(
        "app.services.nutrition_state_service.build_streak_summary",
        side_effect=RuntimeError("streak failed"),
    )
    mocker.patch(
        "app.services.nutrition_state_service.build_ai_summary",
        side_effect=RuntimeError("ai failed"),
    )

    response = asyncio.run(
        nutrition_state_service.get_nutrition_state(
            "user-1",
            day_key="2026-03-18",
            now=NOW,
        )
    )

    assert response.habits.available is False
    assert response.streak.available is False
    assert response.ai.available is False
    assert response.consumed.kcal == 400


# ---------------------------------------------------------------------------
# Day resolution — dayKey vs timestamp fallback
# ---------------------------------------------------------------------------


def test_get_nutrition_state_uses_deterministic_default_day_handling(
    mocker: MockerFixture,
) -> None:
    """When no explicit day_key param, day is derived from ``now``.
    Meals without dayKey fall back to timestamp-based day derivation."""
    client, _ = _mock_firestore(
        mocker,
        profile={"calorieTarget": 1800},
        meals=[
            _meal(
                meal_id="meal-1",
                timestamp="2026-03-18T01:00:00Z",
                kcal=500,
                protein=20,
            ),
            _meal(
                meal_id="meal-2",
                timestamp="2026-03-17T23:00:00Z",
                kcal=300,
                protein=10,
            ),
        ],
    )
    mocker.patch("app.services.nutrition_state_service.settings.STATE_ENABLED", True)
    mocker.patch("app.services.nutrition_state_service.get_firestore", return_value=client)
    mocker.patch(
        "app.services.nutrition_state_service.ai_credits_service.get_credits_status",
        return_value=_credits_status(),
    )

    response = asyncio.run(
        nutrition_state_service.get_nutrition_state("user-1", now=NOW)
    )

    assert response.dayKey == "2026-03-18"
    assert response.consumed.kcal == 500


# ---------------------------------------------------------------------------
# Deleted meals excluded
# ---------------------------------------------------------------------------


def test_get_nutrition_state_excludes_deleted_meals(mocker: MockerFixture) -> None:
    """Deleted meals should not contribute to consumed macros or quality."""
    client, _ = _mock_firestore(
        mocker,
        profile={"calorieTarget": 2000},
        meals=[
            _meal(
                meal_id="active-1",
                day_key="2026-03-18",
                timestamp="2026-03-18T08:00:00Z",
                kcal=600,
                protein=30,
            ),
            _meal(
                meal_id="deleted-1",
                day_key="2026-03-18",
                timestamp="2026-03-18T12:00:00Z",
                kcal=800,
                protein=40,
                deleted=True,
            ),
        ],
    )
    mocker.patch("app.services.nutrition_state_service.settings.STATE_ENABLED", True)
    mocker.patch("app.services.nutrition_state_service.get_firestore", return_value=client)
    mocker.patch(
        "app.services.nutrition_state_service.ai_credits_service.get_credits_status",
        return_value=_credits_status(),
    )

    response = asyncio.run(
        nutrition_state_service.get_nutrition_state(
            "user-1",
            day_key="2026-03-18",
            now=NOW,
        )
    )

    # Only the active meal should be counted
    assert response.consumed.kcal == 600
    assert response.consumed.protein == 30
    assert response.quality.mealsLogged == 1


# ---------------------------------------------------------------------------
# Bounded query verification
# ---------------------------------------------------------------------------


def test_get_nutrition_state_uses_bounded_queries(mocker: MockerFixture) -> None:
    """Verify that the service uses dayKey and timestamp bounded queries
    instead of unbounded .stream()."""
    client, meals_collection = _mock_firestore(
        mocker,
        profile={"calorieTarget": 1800},
        meals=[
            _meal(
                meal_id="meal-1",
                day_key="2026-03-18",
                timestamp="2026-03-18T12:00:00Z",
                kcal=400,
                protein=20,
            )
        ],
    )
    mocker.patch("app.services.nutrition_state_service.settings.STATE_ENABLED", True)
    mocker.patch("app.services.nutrition_state_service.get_firestore", return_value=client)
    mocker.patch(
        "app.services.nutrition_state_service.ai_credits_service.get_credits_status",
        return_value=_credits_status(),
    )

    asyncio.run(
        nutrition_state_service.get_nutrition_state(
            "user-1",
            day_key="2026-03-18",
            now=NOW,
        )
    )

    # Expect exactly 2 calls: dayKey-based query + timestamp-based fallback
    assert len(meals_collection.calls) == 2

    # First query: dayKey range
    first_call = meals_collection.calls[0]
    assert first_call[0] == ("deleted", "==", False)
    assert first_call[1][0] == "dayKey"
    assert first_call[1][1] == ">="
    assert first_call[2][0] == "dayKey"
    assert first_call[2][1] == "<="

    # Second query: timestamp range
    second_call = meals_collection.calls[1]
    assert second_call[0] == ("deleted", "==", False)
    assert second_call[1][0] == "timestamp"
    assert second_call[1][1] == ">="
    assert second_call[2][0] == "timestamp"
    assert second_call[2][1] == "<"


# ---------------------------------------------------------------------------
# dayKey-driven read path — only requested day contributes to consumed
# ---------------------------------------------------------------------------


def test_only_requested_day_contributes_to_consumed(mocker: MockerFixture) -> None:
    """Meals from other days (within the bounded window) must NOT affect
    the consumed/remaining/quality for the requested day."""
    client, _ = _mock_firestore(
        mocker,
        profile={"calorieTarget": 2000},
        meals=[
            _meal(
                meal_id="today-1",
                day_key="2026-03-18",
                timestamp="2026-03-18T12:00:00Z",
                kcal=500,
                protein=25,
            ),
            _meal(
                meal_id="yesterday-1",
                day_key="2026-03-17",
                timestamp="2026-03-17T12:00:00Z",
                kcal=1000,
                protein=50,
            ),
            _meal(
                meal_id="last-week-1",
                day_key="2026-03-11",
                timestamp="2026-03-11T12:00:00Z",
                kcal=2000,
                protein=100,
            ),
        ],
    )
    mocker.patch("app.services.nutrition_state_service.settings.STATE_ENABLED", True)
    mocker.patch("app.services.nutrition_state_service.get_firestore", return_value=client)
    mocker.patch(
        "app.services.nutrition_state_service.ai_credits_service.get_credits_status",
        return_value=_credits_status(),
    )

    response = asyncio.run(
        nutrition_state_service.get_nutrition_state(
            "user-1",
            day_key="2026-03-18",
            now=NOW,
        )
    )

    # Only today-1 should contribute
    assert response.consumed.kcal == 500
    assert response.consumed.protein == 25
    assert response.remaining.kcal == 1500
    assert response.quality.mealsLogged == 1


# ---------------------------------------------------------------------------
# Streak reads from document, not from meal history
# ---------------------------------------------------------------------------


def test_streak_reads_from_document(mocker: MockerFixture) -> None:
    """Streak summary should come from the streak document, not computed
    from meal history."""
    client, _ = _mock_firestore(
        mocker,
        profile={"calorieTarget": 2000},
        meals=[],
        streak={"current": 42, "lastDate": "2026-03-17"},
    )
    mocker.patch("app.services.nutrition_state_service.settings.STATE_ENABLED", True)
    mocker.patch("app.services.nutrition_state_service.get_firestore", return_value=client)
    mocker.patch(
        "app.services.nutrition_state_service.ai_credits_service.get_credits_status",
        return_value=_credits_status(),
    )

    response = asyncio.run(
        nutrition_state_service.get_nutrition_state(
            "user-1",
            day_key="2026-03-18",
            now=NOW,
        )
    )

    assert response.streak.available is True
    assert response.streak.current == 42
    assert response.streak.lastDate == "2026-03-17"


# ---------------------------------------------------------------------------
# Overshoot clamp
# ---------------------------------------------------------------------------


def test_remaining_clamps_overshoot_to_zero(mocker: MockerFixture) -> None:
    """When consumed > target, remaining should be clamped to 0, not negative."""
    client, _ = _mock_firestore(
        mocker,
        profile={"calorieTarget": 1500, "macroTargets": {"proteinGrams": 80}},
        meals=[
            _meal(
                meal_id="big-meal",
                day_key="2026-03-18",
                timestamp="2026-03-18T12:00:00Z",
                kcal=2000,
                protein=120,
            ),
        ],
    )
    mocker.patch("app.services.nutrition_state_service.settings.STATE_ENABLED", True)
    mocker.patch("app.services.nutrition_state_service.get_firestore", return_value=client)
    mocker.patch(
        "app.services.nutrition_state_service.ai_credits_service.get_credits_status",
        return_value=_credits_status(),
    )

    response = asyncio.run(
        nutrition_state_service.get_nutrition_state(
            "user-1",
            day_key="2026-03-18",
            now=NOW,
        )
    )

    assert response.remaining.kcal == 0
    assert response.remaining.protein == 0


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_get_nutrition_state_raises_when_disabled() -> None:
    with pytest.raises(StateDisabledError):
        asyncio.run(nutrition_state_service.get_nutrition_state("user-1"))


def test_get_nutrition_state_wraps_core_firestore_errors(mocker: MockerFixture) -> None:
    client = mocker.Mock()
    user_ref = mocker.Mock()
    user_ref.get.side_effect = GoogleAPICallError("boom")
    client.collection.return_value.document.return_value = user_ref

    mocker.patch("app.services.nutrition_state_service.settings.STATE_ENABLED", True)
    mocker.patch("app.services.nutrition_state_service.get_firestore", return_value=client)

    with pytest.raises(FirestoreServiceError):
        asyncio.run(
            nutrition_state_service.get_nutrition_state(
                "user-1",
                day_key="2026-03-18",
                now=NOW,
            )
        )


# ---------------------------------------------------------------------------
# Response shape regression
# ---------------------------------------------------------------------------


def test_response_contains_all_top_level_fields(mocker: MockerFixture) -> None:
    """Verify the response shape has not regressed — all 8 top-level fields present."""
    client, _ = _mock_firestore(
        mocker,
        profile={"calorieTarget": 2000},
        meals=[],
    )
    mocker.patch("app.services.nutrition_state_service.settings.STATE_ENABLED", True)
    mocker.patch("app.services.nutrition_state_service.get_firestore", return_value=client)
    mocker.patch(
        "app.services.nutrition_state_service.ai_credits_service.get_credits_status",
        return_value=_credits_status(),
    )

    response = asyncio.run(
        nutrition_state_service.get_nutrition_state(
            "user-1",
            day_key="2026-03-18",
            now=NOW,
        )
    )

    data = response.model_dump()
    expected_keys = {
        "computedAt",
        "dayKey",
        "targets",
        "consumed",
        "remaining",
        "quality",
        "habits",
        "streak",
        "ai",
    }
    assert expected_keys == set(data.keys())
