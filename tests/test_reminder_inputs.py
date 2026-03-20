import asyncio
import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from pytest_mock import MockerFixture

from app.schemas.nutrition_state import NutritionStateResponse
from app.services.reminder_inputs import build_reminder_inputs

FIXTURES_DIR = Path(__file__).parent / "contract_fixtures"


def _load_state_fixture() -> NutritionStateResponse:
    payload = json.loads((FIXTURES_DIR / "nutrition_state.json").read_text(encoding="utf-8"))
    return NutritionStateResponse.model_validate(payload)


def test_build_reminder_inputs_marks_already_logged_recently_for_recent_meal(
    mocker: MockerFixture,
) -> None:
    mocker.patch(
        "app.services.reminder_inputs.list_history",
        side_effect=[
            (
                [
                    {
                        "timestamp": "2026-03-18T11:20:00Z",
                        "cloudId": "meal-1",
                        "dayKey": "2026-03-18",
                    }
                ],
                None,
            ),
            ([], None),
        ],
    )
    mocker.patch("app.services.reminder_inputs.list_changes", return_value=([], None))
    mocker.patch("app.services.reminder_inputs.list_notifications", return_value=[])

    inputs = asyncio.run(
        build_reminder_inputs(
            user_id="user-1",
            state=_load_state_fixture(),
            raw_prefs={},
            now_utc=datetime(2026, 3, 18, 12, 0, tzinfo=UTC),
        )
    )

    assert inputs.activity.already_logged_recently is True
    assert inputs.activity.recent_activity_detected is False
    assert inputs.now_local == datetime(2026, 3, 18, 12, 0, tzinfo=UTC)


def test_build_reminder_inputs_ignores_meals_outside_recent_window(
    mocker: MockerFixture,
) -> None:
    mocker.patch(
        "app.services.reminder_inputs.list_history",
        side_effect=[
            (
                [
                    {
                        "timestamp": "2026-03-18T09:59:00Z",
                        "cloudId": "meal-1",
                        "dayKey": "2026-03-18",
                    }
                ],
                None,
            ),
            ([], None),
        ],
    )
    mocker.patch("app.services.reminder_inputs.list_changes", return_value=([], None))
    mocker.patch("app.services.reminder_inputs.list_notifications", return_value=[])

    inputs = asyncio.run(
        build_reminder_inputs(
            user_id="user-1",
            state=_load_state_fixture(),
            raw_prefs={},
            now_utc=datetime(2026, 3, 18, 12, 0, tzinfo=UTC),
        )
    )

    assert inputs.activity.already_logged_recently is False


def test_build_reminder_inputs_maps_current_preferences_and_explicit_limitations(
    mocker: MockerFixture,
) -> None:
    list_history = mocker.patch(
        "app.services.reminder_inputs.list_history",
        side_effect=[
            ([], None),
            ([{"tzOffsetMin": 60, "timestamp": "2026-03-17T17:00:00Z"}], None),
        ],
    )
    mocker.patch("app.services.reminder_inputs.list_changes", return_value=([], None))
    mocker.patch("app.services.reminder_inputs.list_notifications", return_value=[])

    inputs = asyncio.run(
        build_reminder_inputs(
            user_id="user-1",
            state=_load_state_fixture(),
            raw_prefs={
                "smartRemindersEnabled": False,
                "quietHours": {"startHour": 22, "endHour": 7},
            },
            now_utc=datetime(2026, 3, 18, 12, 0, tzinfo=UTC),
        )
    )

    assert inputs.preferences.reminders_enabled is False
    assert inputs.preferences.quiet_hours is not None
    assert inputs.preferences.quiet_hours.start_hour == 22
    assert inputs.preferences.quiet_hours.end_hour == 7
    assert inputs.preferences.first_meal_window is None
    assert inputs.preferences.next_meal_window is None
    assert inputs.preferences.complete_day_window is None
    assert inputs.now_local == datetime(
        2026,
        3,
        18,
        13,
        0,
        tzinfo=timezone(timedelta(minutes=60)),
    )
    assert list_history.call_args_list[0].args == ("user-1",)
    assert list_history.call_args_list[0].kwargs == {
        "limit_count": 5,
        "timestamp_start": "2026-03-18T10:30:00Z",
        "timestamp_end": "2026-03-18T12:00:00Z",
    }
    assert list_history.call_args_list[1].args == ("user-1",)
    assert list_history.call_args_list[1].kwargs == {"limit_count": 1}


def test_build_reminder_inputs_infers_local_offset_from_logged_at_local_min(
    mocker: MockerFixture,
) -> None:
    mocker.patch(
        "app.services.reminder_inputs.list_history",
        side_effect=[
            ([], None),
            (
                [
                    {
                        "timestamp": "2026-03-17T22:30:00Z",
                        "loggedAtLocalMin": 1410,
                    }
                ],
                None,
            ),
        ],
    )
    mocker.patch("app.services.reminder_inputs.list_changes", return_value=([], None))
    mocker.patch("app.services.reminder_inputs.list_notifications", return_value=[])

    inputs = asyncio.run(
        build_reminder_inputs(
            user_id="user-1",
            state=_load_state_fixture(),
            raw_prefs={},
            now_utc=datetime(2026, 3, 18, 12, 0, tzinfo=UTC),
        )
    )

    assert inputs.now_local == datetime(
        2026,
        3,
        18,
        13,
        0,
        tzinfo=timezone(timedelta(minutes=60)),
    )


def test_build_reminder_inputs_derives_preferred_windows_from_enabled_notifications(
    mocker: MockerFixture,
) -> None:
    mocker.patch(
        "app.services.reminder_inputs.list_history",
        side_effect=[
            ([], None),
            ([{"tzOffsetMin": 60, "timestamp": "2026-03-17T17:00:00Z"}], None),
        ],
    )
    mocker.patch("app.services.reminder_inputs.list_changes", return_value=([], None))
    mocker.patch(
        "app.services.reminder_inputs.list_notifications",
        return_value=[
            {
                "id": "meal-lunch",
                "type": "meal_reminder",
                "enabled": True,
                "time": {"hour": 13, "minute": 0},
                "mealKind": "lunch",
            },
            {
                "id": "day-fill",
                "type": "day_fill",
                "enabled": True,
                "time": {"hour": 20, "minute": 0},
                "mealKind": None,
            },
        ],
    )

    inputs = asyncio.run(
        build_reminder_inputs(
            user_id="user-1",
            state=_load_state_fixture(),
            raw_prefs={},
            now_utc=datetime(2026, 3, 18, 12, 15, tzinfo=UTC),
        )
    )

    assert inputs.preferences.first_meal_window is None
    assert inputs.preferences.next_meal_window is not None
    assert inputs.preferences.next_meal_window.start_min == 720
    assert inputs.preferences.next_meal_window.end_min == 840
    assert inputs.preferences.complete_day_window is not None
    assert inputs.preferences.complete_day_window.start_min == 1140
    assert inputs.preferences.complete_day_window.end_min == 1260


def test_build_reminder_inputs_uses_future_preferred_window_for_same_day(
    mocker: MockerFixture,
) -> None:
    mocker.patch(
        "app.services.reminder_inputs.list_history",
        side_effect=[
            ([], None),
            ([{"tzOffsetMin": 60, "timestamp": "2026-03-17T17:00:00Z"}], None),
        ],
    )
    mocker.patch("app.services.reminder_inputs.list_changes", return_value=([], None))
    mocker.patch(
        "app.services.reminder_inputs.list_notifications",
        return_value=[
            {
                "id": "meal-lunch",
                "type": "meal_reminder",
                "enabled": True,
                "time": {"hour": 13, "minute": 0},
                "mealKind": "lunch",
            }
        ],
    )

    inputs = asyncio.run(
        build_reminder_inputs(
            user_id="user-1",
            state=_load_state_fixture(),
            raw_prefs={},
            now_utc=datetime(2026, 3, 18, 9, 0, tzinfo=UTC),
        )
    )

    assert inputs.preferences.reminders_enabled is True
    assert inputs.preferences.next_meal_window is not None
    assert inputs.preferences.next_meal_window.start_min == 720
    assert inputs.preferences.next_meal_window.end_min == 840


def test_build_reminder_inputs_marks_recent_activity_for_recent_backfill_change(
    mocker: MockerFixture,
) -> None:
    mocker.patch(
        "app.services.reminder_inputs.list_history",
        side_effect=[
            ([], None),
            ([], None),
        ],
    )
    mocker.patch(
        "app.services.reminder_inputs.list_changes",
        return_value=(
            [
                {
                    "timestamp": "2026-03-18T06:30:00Z",
                    "updatedAt": "2026-03-18T11:40:00Z",
                    "cloudId": "meal-1",
                }
            ],
            None,
        ),
    )
    mocker.patch("app.services.reminder_inputs.list_notifications", return_value=[])

    inputs = asyncio.run(
        build_reminder_inputs(
            user_id="user-1",
            state=_load_state_fixture(),
            raw_prefs={},
            now_utc=datetime(2026, 3, 18, 12, 0, tzinfo=UTC),
        )
    )

    assert inputs.activity.already_logged_recently is False
    assert inputs.activity.recent_activity_detected is True
