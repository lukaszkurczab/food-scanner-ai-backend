import asyncio

import pytest
from google.api_core.exceptions import GoogleAPICallError
from pytest_mock import MockerFixture

from app.core.exceptions import FirestoreServiceError
from app.services import notification_plan_service
from app.services.notification_plan_service import NotificationPlan, NotificationTime


def test_evaluate_notification_plan_marks_meal_reminder_without_meal_kind(
) -> None:
    plan = NotificationPlan(
        id="n1",
        type="meal_reminder",
        enabled=True,
        text=None,
        time=NotificationTime(hour=12, minute=0),
        days=[1, 2, 3],
        meal_kind=None,
        kcal_by_hour=None,
        should_schedule=False,
    )

    evaluated = notification_plan_service._evaluate_notification_plan(
        plan,
        ai_style="friendly",
        target_kcal=2000,
        meals=[],
    )

    assert evaluated.should_schedule is True


def test_evaluate_notification_plan_marks_calorie_goal_and_missing_kcal() -> None:
    plan = NotificationPlan(
        id="n2",
        type="calorie_goal",
        enabled=True,
        text=None,
        time=NotificationTime(hour=18, minute=0),
        days=[1, 2, 3],
        meal_kind=None,
        kcal_by_hour=None,
        should_schedule=False,
    )

    evaluated = notification_plan_service._evaluate_notification_plan(
        plan,
        ai_style="friendly",
        target_kcal=2000,
        meals=[{"totals": {"kcal": 1250}}],
    )

    assert evaluated.should_schedule is True
    assert evaluated.missing_kcal == 750


def test_evaluate_notification_plan_skips_day_fill_when_meals_exist() -> None:
    plan = NotificationPlan(
        id="n3",
        type="day_fill",
        enabled=True,
        text=None,
        time=NotificationTime(hour=20, minute=0),
        days=[1, 2, 3],
        meal_kind=None,
        kcal_by_hour=None,
        should_schedule=False,
    )

    evaluated = notification_plan_service._evaluate_notification_plan(
        plan,
        ai_style="friendly",
        target_kcal=2000,
        meals=[{"mealId": "meal-1"}],
    )

    assert evaluated.should_schedule is False


def test_get_notification_plan_wraps_firestore_errors(mocker: MockerFixture) -> None:
    client = mocker.Mock()
    user_ref = mocker.Mock()
    client.collection.return_value = mocker.Mock(document=mocker.Mock(return_value=user_ref))
    user_ref.get.side_effect = GoogleAPICallError("boom")
    mocker.patch(
        "app.services.notification_plan_service.get_firestore",
        return_value=client,
    )

    with pytest.raises(FirestoreServiceError):
        asyncio.run(
            notification_plan_service.get_notification_plan(
                "user-1",
                start_iso="2026-03-03T00:00:00.000Z",
                end_iso="2026-03-03T23:59:59.999Z",
            )
        )
