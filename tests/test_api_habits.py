from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from app.api.v2.router import router as v2_router
from app.core.exceptions import HabitsDisabledError
from app.schemas.habits import (
    HabitBehavior,
    HabitDataQuality,
    HabitSignalsResponse,
    MealTypeCoverage14,
    ProteinDaysHit14,
)


def create_test_client() -> TestClient:
    app = FastAPI()
    app.include_router(v2_router, prefix="/api/v2")
    return TestClient(app)


def test_get_user_habits_returns_response_shape(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch(
        "app.api.routes.habits.get_habit_signals",
        return_value=HabitSignalsResponse(
            computedAt=datetime(2026, 3, 18, 12, 0, tzinfo=UTC)
            .isoformat()
            .replace("+00:00", "Z"),
            behavior=HabitBehavior(
                loggingDays7=5,
                loggingConsistency28=0.6,
                avgMealsPerLoggedDay14=2.2,
                mealTypeCoverage14=MealTypeCoverage14(
                    breakfast=True,
                    lunch=True,
                    dinner=False,
                    snack=True,
                    other=False,
                    coveredCount=3,
                ),
                kcalAdherence14=0.94,
                kcalUnderTargetRatio14=0.3,
                proteinDaysHit14=ProteinDaysHit14(
                    hitDays=4,
                    eligibleDays=5,
                    unknownDays=0,
                    ratio=0.8,
                ),
            ),
            dataQuality=HabitDataQuality(daysWithUnknownMealDetails14=1),
            topRisk="low_protein_consistency",
            coachPriority="protein_consistency",
        ),
    )
    client = create_test_client()

    response = client.get("/api/v2/users/me/habits", headers=auth_headers("user-1"))

    assert response.status_code == 200
    assert response.json() == {
        "computedAt": "2026-03-18T12:00:00Z",
        "windowDays": {"recentActivity": 7, "adherence": 14, "consistency": 28},
        "behavior": {
            "loggingDays7": 5,
            "loggingConsistency28": 0.6,
            "avgMealsPerLoggedDay14": 2.2,
            "mealTypeCoverage14": {
                "breakfast": True,
                "lunch": True,
                "dinner": False,
                "snack": True,
                "other": False,
                "coveredCount": 3,
            },
            "kcalAdherence14": 0.94,
            "kcalUnderTargetRatio14": 0.3,
            "proteinDaysHit14": {
                "hitDays": 4,
                "eligibleDays": 5,
                "unknownDays": 0,
                "ratio": 0.8,
            },
        },
        "dataQuality": {"daysWithUnknownMealDetails14": 1},
        "topRisk": "low_protein_consistency",
        "coachPriority": "protein_consistency",
    }


def test_get_user_habits_returns_503_when_feature_flag_is_disabled(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch(
        "app.api.routes.habits.get_habit_signals",
        side_effect=HabitsDisabledError("disabled"),
    )
    client = create_test_client()

    response = client.get("/api/v2/users/me/habits", headers=auth_headers("user-1"))

    assert response.status_code == 503
    assert response.json() == {"detail": "Habit signals are disabled"}
