import asyncio
import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from pydantic import ValidationError as PydanticValidationError

from app.core.exceptions import (
    FirestoreServiceError,
    ReminderDecisionContractError,
    ReminderUnavailableError,
    SmartRemindersDisabledError,
)
from app.schemas.nutrition_state import NutritionStateResponse
from app.schemas.reminders import ReminderDecision
from app.services.reminder_inputs import ReminderInputs
from app.services.reminder_rule_engine import ReminderActivityInput, ReminderPreferencesInput, ReminderQuietHours
from app.services.reminder_service import get_reminder_decision

FIXTURES_DIR = Path(__file__).parent / "contract_fixtures"


def _load_state_fixture() -> NutritionStateResponse:
    payload = json.loads((FIXTURES_DIR / "nutrition_state.json").read_text(encoding="utf-8"))
    return NutritionStateResponse.model_validate(payload)


def _load_decision_fixture() -> ReminderDecision:
    payload = json.loads((FIXTURES_DIR / "reminder_decision.json").read_text(encoding="utf-8"))
    return ReminderDecision.model_validate(payload)


def test_get_reminder_decision_returns_rule_engine_output(
    mocker: MockerFixture,
) -> None:
    state = _load_state_fixture()
    decision = _load_decision_fixture()
    get_state = mocker.patch(
        "app.services.reminder_service.get_nutrition_state",
        return_value=state,
    )
    get_prefs = mocker.patch(
        "app.services.reminder_service.get_notification_prefs",
        return_value={
            "smartRemindersEnabled": False,
            "quietHours": {"startHour": 22, "endHour": 7},
        },
    )
    build_inputs = mocker.patch(
        "app.services.reminder_service.build_reminder_inputs",
        return_value=ReminderInputs(
            preferences=ReminderPreferencesInput(
                reminders_enabled=False,
                quiet_hours=ReminderQuietHours(start_hour=22, end_hour=7),
            ),
            activity=ReminderActivityInput(already_logged_recently=True),
            now_local=datetime(
                2026,
                3,
                18,
                13,
                0,
                tzinfo=timezone(timedelta(minutes=60)),
            ),
        ),
    )
    evaluate = mocker.patch(
        "app.services.reminder_service.evaluate_reminder_decision",
        return_value=decision,
    )
    record_send = mocker.patch(
        "app.services.reminder_service.record_send_decision",
    )
    mocker.patch("app.services.reminder_service.settings.SMART_REMINDERS_ENABLED", True)
    mocker.patch(
        "app.services.reminder_service.utc_now",
        return_value=datetime(2026, 3, 18, 12, 0, tzinfo=UTC),
    )

    response = asyncio.run(get_reminder_decision("user-1", day_key="2026-03-18"))

    assert response == decision
    record_send.assert_called_once_with("user-1", state.dayKey)
    get_state.assert_called_once_with("user-1", day_key="2026-03-18")
    get_prefs.assert_called_once_with("user-1")
    build_inputs.assert_called_once_with(
        user_id="user-1",
        state=state,
        raw_prefs={
            "smartRemindersEnabled": False,
            "quietHours": {"startHour": 22, "endHour": 7},
        },
        now_utc=datetime(2026, 3, 18, 12, 0, tzinfo=UTC),
        tz_offset_min=None,
    )
    evaluate.assert_called_once()
    assert evaluate.call_args.kwargs["preferences"].reminders_enabled is False
    assert evaluate.call_args.kwargs["preferences"].quiet_hours is not None
    assert evaluate.call_args.kwargs["activity"].already_logged_recently is True
    assert evaluate.call_args.kwargs["context"].now_local == datetime(
        2026,
        3,
        18,
        13,
        0,
        tzinfo=timezone(timedelta(minutes=60)),
    )


def test_get_reminder_decision_passes_tz_offset_min_to_input_builder(
    mocker: MockerFixture,
) -> None:
    state = _load_state_fixture()
    decision = _load_decision_fixture()
    mocker.patch(
        "app.services.reminder_service.get_nutrition_state",
        return_value=state,
    )
    mocker.patch(
        "app.services.reminder_service.get_notification_prefs",
        return_value={},
    )
    build_inputs = mocker.patch(
        "app.services.reminder_service.build_reminder_inputs",
        return_value=ReminderInputs(
            preferences=ReminderPreferencesInput(reminders_enabled=True),
            activity=ReminderActivityInput(),
            now_local=datetime(
                2026, 3, 18, 14, 0,
                tzinfo=timezone(timedelta(minutes=120)),
            ),
        ),
    )
    mocker.patch(
        "app.services.reminder_service.evaluate_reminder_decision",
        return_value=decision,
    )
    mocker.patch("app.services.reminder_service.record_send_decision")
    mocker.patch("app.services.reminder_service.settings.SMART_REMINDERS_ENABLED", True)
    mocker.patch(
        "app.services.reminder_service.utc_now",
        return_value=datetime(2026, 3, 18, 12, 0, tzinfo=UTC),
    )

    asyncio.run(get_reminder_decision("user-1", tz_offset_min=120))

    assert build_inputs.call_args.kwargs["tz_offset_min"] == 120


def test_get_reminder_decision_raises_when_feature_is_disabled(
    mocker: MockerFixture,
) -> None:
    mocker.patch("app.services.reminder_service.settings.SMART_REMINDERS_ENABLED", False)

    with pytest.raises(SmartRemindersDisabledError):
        asyncio.run(get_reminder_decision("user-1"))


def test_get_reminder_decision_raises_when_habits_foundation_is_unavailable(
    mocker: MockerFixture,
) -> None:
    state = _load_state_fixture()
    state.habits.available = False
    state.meta.componentStatus.habits = "error"
    mocker.patch("app.services.reminder_service.settings.SMART_REMINDERS_ENABLED", True)
    mocker.patch(
        "app.services.reminder_service.get_nutrition_state",
        return_value=state,
    )

    with pytest.raises(ReminderUnavailableError):
        asyncio.run(get_reminder_decision("user-1"))


def test_get_reminder_decision_propagates_state_failures_instead_of_masking_as_noop(
    mocker: MockerFixture,
) -> None:
    mocker.patch("app.services.reminder_service.settings.SMART_REMINDERS_ENABLED", True)
    mocker.patch(
        "app.services.reminder_service.get_nutrition_state",
        side_effect=FirestoreServiceError("state failed"),
    )

    with pytest.raises(FirestoreServiceError):
        asyncio.run(get_reminder_decision("user-1"))


def test_get_reminder_decision_propagates_preference_failures_instead_of_masking_as_noop(
    mocker: MockerFixture,
) -> None:
    state = _load_state_fixture()
    mocker.patch("app.services.reminder_service.settings.SMART_REMINDERS_ENABLED", True)
    mocker.patch(
        "app.services.reminder_service.get_nutrition_state",
        return_value=state,
    )
    mocker.patch(
        "app.services.reminder_service.get_notification_prefs",
        side_effect=FirestoreServiceError("prefs failed"),
    )

    with pytest.raises(FirestoreServiceError):
        asyncio.run(get_reminder_decision("user-1"))


def test_get_reminder_decision_propagates_input_builder_failures_instead_of_masking_as_noop(
    mocker: MockerFixture,
) -> None:
    state = _load_state_fixture()
    mocker.patch("app.services.reminder_service.settings.SMART_REMINDERS_ENABLED", True)
    mocker.patch(
        "app.services.reminder_service.get_nutrition_state",
        return_value=state,
    )
    mocker.patch(
        "app.services.reminder_service.get_notification_prefs",
        return_value={},
    )
    mocker.patch(
        "app.services.reminder_service.build_reminder_inputs",
        side_effect=FirestoreServiceError("recent meal lookup failed"),
    )

    with pytest.raises(FirestoreServiceError):
        asyncio.run(get_reminder_decision("user-1"))


def test_get_reminder_decision_wraps_contract_violation_as_contract_error(
    mocker: MockerFixture,
) -> None:
    """If the rule engine somehow produces a decision that fails Pydantic
    validation (e.g. microsecond timestamps exceeding max_length=20),
    it must surface as ReminderDecisionContractError — not as a bare
    PydanticValidationError or, worse, a ValueError mapped to 400."""
    state = _load_state_fixture()
    mocker.patch("app.services.reminder_service.settings.SMART_REMINDERS_ENABLED", True)
    mocker.patch(
        "app.services.reminder_service.get_nutrition_state",
        return_value=state,
    )
    mocker.patch(
        "app.services.reminder_service.get_notification_prefs",
        return_value={},
    )
    mocker.patch(
        "app.services.reminder_service.build_reminder_inputs",
        return_value=ReminderInputs(
            preferences=ReminderPreferencesInput(reminders_enabled=False),
            activity=ReminderActivityInput(),
            now_local=datetime(2026, 3, 18, 13, 0, tzinfo=UTC),
        ),
    )
    mocker.patch(
        "app.services.reminder_service.evaluate_reminder_decision",
        side_effect=PydanticValidationError.from_exception_data(
            title="ReminderDecision",
            line_errors=[
                {
                    "type": "string_too_long",
                    "loc": ("computedAt",),
                    "msg": "String should have at most 20 characters",
                    "input": "2026-03-18T13:00:33.999999Z",
                    "ctx": {"max_length": 20},
                }
            ],
        ),
    )

    with pytest.raises(ReminderDecisionContractError) as exc_info:
        asyncio.run(get_reminder_decision("user-1"))

    assert "invalid decision" in str(exc_info.value).lower()


def test_get_reminder_decision_does_not_record_send_for_suppress(
    mocker: MockerFixture,
) -> None:
    """record_send_decision must NOT be called when the decision is suppress."""
    state = _load_state_fixture()
    suppress_decision = ReminderDecision(
        dayKey="2026-03-18",
        computedAt="2026-03-18T12:00:00Z",
        decision="suppress",
        reasonCodes=["reminders_disabled"],
        confidence=1.0,
        validUntil="2026-03-18T23:59:59Z",
    )
    mocker.patch("app.services.reminder_service.settings.SMART_REMINDERS_ENABLED", True)
    mocker.patch(
        "app.services.reminder_service.get_nutrition_state",
        return_value=state,
    )
    mocker.patch(
        "app.services.reminder_service.get_notification_prefs",
        return_value={},
    )
    mocker.patch(
        "app.services.reminder_service.build_reminder_inputs",
        return_value=ReminderInputs(
            preferences=ReminderPreferencesInput(reminders_enabled=False),
            activity=ReminderActivityInput(),
            now_local=datetime(2026, 3, 18, 12, 0, tzinfo=UTC),
        ),
    )
    mocker.patch(
        "app.services.reminder_service.evaluate_reminder_decision",
        return_value=suppress_decision,
    )
    record_send = mocker.patch(
        "app.services.reminder_service.record_send_decision",
    )
    mocker.patch(
        "app.services.reminder_service.utc_now",
        return_value=datetime(2026, 3, 18, 12, 0, tzinfo=UTC),
    )

    response = asyncio.run(get_reminder_decision("user-1"))

    assert response.decision == "suppress"
    record_send.assert_not_called()
