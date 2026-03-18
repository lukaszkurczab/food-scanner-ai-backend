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


def _mock_firestore(
    mocker: MockerFixture,
    *,
    profile: dict[str, Any] | None,
    meals: list[dict[str, Any]],
):
    user_snapshot = mocker.Mock()
    user_snapshot.exists = profile is not None
    user_snapshot.to_dict.return_value = profile or {}

    meal_snapshots = []
    for meal in meals:
        snapshot = mocker.Mock()
        snapshot.to_dict.return_value = meal
        meal_snapshots.append(snapshot)

    meals_collection = mocker.Mock()
    meals_collection.stream.return_value = meal_snapshots

    user_ref = mocker.Mock()
    user_ref.get.return_value = user_snapshot
    user_ref.collection.return_value = meals_collection

    client = mocker.Mock()
    client.collection.return_value.document.return_value = user_ref
    return client


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
    client = _mock_firestore(mocker, profile=profile, meals=meals)
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
    assert response.ai.available is True
    assert response.ai.usedThisPeriod == 160


def test_get_nutrition_state_returns_empty_day_defaults(mocker: MockerFixture) -> None:
    client = _mock_firestore(
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


def test_get_nutrition_state_handles_missing_targets(mocker: MockerFixture) -> None:
    client = _mock_firestore(
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


def test_get_nutrition_state_degrades_gracefully_for_subservices(
    mocker: MockerFixture,
) -> None:
    client = _mock_firestore(
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


def test_get_nutrition_state_uses_deterministic_default_day_handling(
    mocker: MockerFixture,
) -> None:
    client = _mock_firestore(
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
