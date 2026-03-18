from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from app.core.exceptions import StateDisabledError
from app.main import app
from app.schemas.ai_credits import CreditCosts
from app.schemas.nutrition_state import (
    NutritionAiSummary,
    NutritionConsumed,
    NutritionHabitsSummary,
    NutritionQuality,
    NutritionRemaining,
    NutritionStateResponse,
    NutritionStreakSummary,
    NutritionTargets,
)

client = TestClient(app)


def test_get_nutrition_state_returns_backend_payload(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch(
        "app.api.routes.nutrition_state.get_nutrition_state",
        return_value=NutritionStateResponse(
            computedAt="2026-03-18T09:30:00Z",
            dayKey="2026-03-18",
            targets=NutritionTargets(kcal=2000, protein=120, carbs=None, fat=None),
            consumed=NutritionConsumed(kcal=1200, protein=75, carbs=100, fat=30),
            remaining=NutritionRemaining(kcal=800, protein=45, carbs=None, fat=None),
            quality=NutritionQuality(
                mealsLogged=2,
                missingNutritionMeals=0,
                dataCompletenessScore=1,
            ),
            habits=NutritionHabitsSummary(available=False),
            streak=NutritionStreakSummary(available=True, current=3, lastDate="2026-03-18"),
            ai=NutritionAiSummary(
                available=True,
                tier="free",
                balance=91,
                allocation=100,
                usedThisPeriod=9,
                periodStartAt="2026-03-01T00:00:00Z",
                periodEndAt="2026-04-01T00:00:00Z",
                costs=CreditCosts(chat=1, textMeal=1, photo=5),
            ),
        ),
    )

    response = client.get(
        "/api/v2/users/me/state?day=2026-03-18",
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json()["dayKey"] == "2026-03-18"
    assert response.json()["consumed"]["kcal"] == 1200.0
    assert response.json()["streak"]["current"] == 3
    assert response.json()["ai"]["usedThisPeriod"] == 9


def test_get_nutrition_state_returns_503_when_disabled(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch(
        "app.api.routes.nutrition_state.get_nutrition_state",
        side_effect=StateDisabledError("disabled"),
    )

    response = client.get("/api/v2/users/me/state", headers=auth_headers("user-1"))

    assert response.status_code == 503
    assert response.json() == {"detail": "Nutrition state is disabled"}
