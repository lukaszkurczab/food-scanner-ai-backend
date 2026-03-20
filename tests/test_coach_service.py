import asyncio
import json
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from app.core.exceptions import CoachUnavailableError
from app.schemas.nutrition_state import NutritionStateResponse
from app.services.coach_service import get_coach_response

FIXTURES_DIR = Path(__file__).parent / "contract_fixtures"


def _load_state_fixture() -> NutritionStateResponse:
    payload = json.loads((FIXTURES_DIR / "nutrition_state.json").read_text(encoding="utf-8"))
    return NutritionStateResponse.model_validate(payload)


def test_get_coach_response_returns_top_insight_and_metadata(
    mocker: MockerFixture,
) -> None:
    state = _load_state_fixture()
    state.habits.topRisk = "under_logging"
    state.habits.behavior.validLoggingDays7 = 2
    mocker.patch(
        "app.services.coach_service.get_nutrition_state",
        return_value=state,
    )

    response = asyncio.run(get_coach_response("user-1", day_key="2026-03-18"))

    assert response.dayKey == "2026-03-18"
    assert response.computedAt == "2026-03-18T12:00:00Z"
    assert response.source == "rules"
    assert response.topInsight is not None
    assert response.topInsight.type == "under_logging"
    assert response.meta.available is True
    assert response.meta.emptyReason is None
    assert response.meta.isDegraded is False


def test_get_coach_response_returns_no_data_empty_reason(
    mocker: MockerFixture,
) -> None:
    state = _load_state_fixture()
    state.quality.mealsLogged = 0
    mocker.patch(
        "app.services.coach_service.get_nutrition_state",
        return_value=state,
    )

    response = asyncio.run(get_coach_response("user-1"))

    assert response.topInsight is None
    assert response.insights == []
    assert response.meta.available is True
    assert response.meta.emptyReason == "no_data"


def test_get_coach_response_returns_insufficient_data_empty_reason(
    mocker: MockerFixture,
) -> None:
    state = _load_state_fixture()
    state.quality.mealsLogged = 1
    state.habits.behavior.validLoggingDays7 = 1
    state.habits.behavior.dayCoverage14.validLoggedDays = 1
    mocker.patch(
        "app.services.coach_service.get_nutrition_state",
        return_value=state,
    )

    response = asyncio.run(get_coach_response("user-1"))

    assert response.topInsight is None
    assert response.insights == []
    assert response.meta.emptyReason == "insufficient_data"


def test_get_coach_response_marks_non_critical_foundation_degradation(
    mocker: MockerFixture,
) -> None:
    state = _load_state_fixture()
    state.meta.componentStatus.streak = "error"
    mocker.patch(
        "app.services.coach_service.get_nutrition_state",
        return_value=state,
    )

    response = asyncio.run(get_coach_response("user-1"))

    assert response.meta.isDegraded is True


def test_get_coach_response_raises_when_habits_foundation_is_unavailable(
    mocker: MockerFixture,
) -> None:
    state = _load_state_fixture()
    state.habits.available = False
    state.meta.componentStatus.habits = "error"
    mocker.patch(
        "app.services.coach_service.get_nutrition_state",
        return_value=state,
    )

    with pytest.raises(CoachUnavailableError):
        asyncio.run(get_coach_response("user-1"))
