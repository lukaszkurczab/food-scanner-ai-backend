import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.schemas.nutrition_state import NutritionStateResponse
from app.services.reminder_rule_engine import (
    ReminderActivityInput,
    ReminderContextInput,
    ReminderPreferencesInput,
    ReminderQuietHours,
    ReminderWindow,
    evaluate_reminder_decision,
)

FIXTURES_DIR = Path(__file__).parent / "contract_fixtures"


def _load_state_fixture() -> NutritionStateResponse:
    payload = json.loads((FIXTURES_DIR / "nutrition_state.json").read_text(encoding="utf-8"))
    return NutritionStateResponse.model_validate(payload)


def _context(hour: int, minute: int) -> ReminderContextInput:
    return ReminderContextInput(now_local=datetime(2026, 3, 18, hour, minute, tzinfo=UTC))


@pytest.mark.parametrize(
    ("preferences", "activity", "expected_reason_codes", "context"),
    [
        (
            ReminderPreferencesInput(reminders_enabled=False),
            ReminderActivityInput(),
            ["reminders_disabled"],
            _context(13, 0),
        ),
        (
            ReminderPreferencesInput(
                quiet_hours=ReminderQuietHours(start_hour=22, end_hour=7)
            ),
            ReminderActivityInput(),
            ["quiet_hours"],
            _context(23, 0),
        ),
        (
            ReminderPreferencesInput(),
            ReminderActivityInput(already_logged_recently=True),
            ["already_logged_recently"],
            _context(13, 0),
        ),
        (
            ReminderPreferencesInput(),
            ReminderActivityInput(recent_activity_detected=True),
            ["recent_activity_detected"],
            _context(13, 0),
        ),
        (
            ReminderPreferencesInput(),
            ReminderActivityInput(daily_send_count=3),
            ["frequency_cap_reached"],
            _context(13, 0),
        ),
    ],
)
def test_hard_suppressions_take_precedence(
    preferences: ReminderPreferencesInput,
    activity: ReminderActivityInput,
    expected_reason_codes: list[str],
    context: ReminderContextInput,
) -> None:
    state = _load_state_fixture()
    state.quality.mealsLogged = 1
    state.quality.dataCompletenessScore = 1.0

    decision = evaluate_reminder_decision(
        state=state,
        preferences=preferences,
        activity=activity,
        context=context,
    )

    assert decision.decision == "suppress"
    assert decision.kind is None
    assert decision.reasonCodes == expected_reason_codes
    assert decision.confidence == 1.0


def test_send_first_meal_when_day_empty_and_window_is_open() -> None:
    state = _load_state_fixture()
    state.quality.mealsLogged = 0
    state.quality.dataCompletenessScore = 1.0

    decision = evaluate_reminder_decision(
        state=state,
        preferences=ReminderPreferencesInput(
            first_meal_window=ReminderWindow(start_min=450, end_min=570)
        ),
        activity=ReminderActivityInput(),
        context=_context(8, 20),
    )

    assert decision.decision == "send"
    assert decision.kind == "log_first_meal"
    assert decision.scheduledAtUtc == "2026-03-18T08:20:00Z"
    assert decision.reasonCodes == [
        "preferred_window_open",
        "habit_window_match",
        "day_empty",
        "logging_usually_happens_now",
    ]
    assert decision.confidence == 0.87


def test_send_first_meal_for_future_preferred_window() -> None:
    state = _load_state_fixture()
    state.quality.mealsLogged = 0
    state.quality.dataCompletenessScore = 1.0

    decision = evaluate_reminder_decision(
        state=state,
        preferences=ReminderPreferencesInput(
            first_meal_window=ReminderWindow(start_min=330, end_min=390)
        ),
        activity=ReminderActivityInput(),
        context=_context(5, 0),
    )

    assert decision.decision == "send"
    assert decision.kind == "log_first_meal"
    assert decision.scheduledAtUtc == "2026-03-18T05:30:00Z"
    assert decision.reasonCodes == [
        "preferred_window_today",
        "day_empty",
    ]


def test_send_next_meal_for_partially_logged_day_in_habit_window() -> None:
    state = _load_state_fixture()
    state.quality.mealsLogged = 1
    state.quality.dataCompletenessScore = 1.0

    decision = evaluate_reminder_decision(
        state=state,
        preferences=ReminderPreferencesInput(),
        activity=ReminderActivityInput(),
        context=_context(13, 0),
    )

    assert decision.decision == "send"
    assert decision.kind == "log_next_meal"
    assert decision.scheduledAtUtc == "2026-03-18T13:00:00Z"
    assert decision.reasonCodes == [
        "habit_window_match",
        "day_partially_logged",
        "logging_usually_happens_now",
    ]
    assert decision.confidence == 0.84


def test_send_next_meal_for_future_habit_window() -> None:
    state = _load_state_fixture()
    state.quality.mealsLogged = 1
    state.quality.dataCompletenessScore = 1.0

    decision = evaluate_reminder_decision(
        state=state,
        preferences=ReminderPreferencesInput(),
        activity=ReminderActivityInput(),
        context=_context(10, 0),
    )

    assert decision.decision == "send"
    assert decision.kind == "log_next_meal"
    assert decision.scheduledAtUtc == "2026-03-18T11:30:00Z"
    assert decision.reasonCodes == [
        "habit_window_today",
        "day_partially_logged",
    ]


def test_send_complete_day_for_later_incomplete_day() -> None:
    state = _load_state_fixture()
    state.quality.mealsLogged = 2
    state.quality.dataCompletenessScore = 0.8

    decision = evaluate_reminder_decision(
        state=state,
        preferences=ReminderPreferencesInput(
            complete_day_window=ReminderWindow(start_min=1110, end_min=1230)
        ),
        activity=ReminderActivityInput(),
        context=_context(19, 30),
    )

    assert decision.decision == "send"
    assert decision.kind == "complete_day"
    assert decision.scheduledAtUtc == "2026-03-18T19:30:00Z"
    assert decision.reasonCodes == [
        "preferred_window_open",
        "habit_window_match",
        "day_partially_logged",
        "logging_usually_happens_now",
    ]
    assert decision.confidence == 0.8


def test_send_uses_canonical_utc_when_local_context_has_offset() -> None:
    state = _load_state_fixture()
    state.quality.mealsLogged = 1
    state.quality.dataCompletenessScore = 1.0

    decision = evaluate_reminder_decision(
        state=state,
        preferences=ReminderPreferencesInput(),
        activity=ReminderActivityInput(),
        context=ReminderContextInput(
            now_local=datetime(
                2026,
                3,
                18,
                13,
                0,
                tzinfo=timezone(timedelta(hours=2)),
            )
        ),
    )

    assert decision.decision == "send"
    assert decision.kind == "log_next_meal"
    assert decision.scheduledAtUtc == "2026-03-18T11:00:00Z"


def test_noop_when_signal_is_insufficient() -> None:
    state = _load_state_fixture()
    state.quality.mealsLogged = 0
    state.quality.dataCompletenessScore = 0.2
    state.habits.behavior.validLoggingDays7 = 1
    state.habits.behavior.dayCoverage14.validLoggedDays = 1
    state.habits.behavior.timingPatterns14.available = False
    state.habits.behavior.timingPatterns14.observedDays = 0

    decision = evaluate_reminder_decision(
        state=state,
        preferences=ReminderPreferencesInput(
            first_meal_window=ReminderWindow(start_min=450, end_min=570)
        ),
        activity=ReminderActivityInput(),
        context=_context(8, 20),
    )

    assert decision.decision == "noop"
    assert decision.kind is None
    assert decision.reasonCodes == ["insufficient_signal"]


def test_noop_when_day_is_already_complete() -> None:
    state = _load_state_fixture()

    decision = evaluate_reminder_decision(
        state=state,
        preferences=ReminderPreferencesInput(),
        activity=ReminderActivityInput(),
        context=_context(20, 0),
    )

    assert decision.decision == "noop"
    assert decision.kind is None
    assert decision.reasonCodes == ["day_already_complete"]
    assert decision.confidence == 0.98


def test_reason_codes_are_deterministic_for_combined_suppressions() -> None:
    state = _load_state_fixture()
    state.quality.mealsLogged = 1

    decision = evaluate_reminder_decision(
        state=state,
        preferences=ReminderPreferencesInput(
            reminders_enabled=False,
            quiet_hours=ReminderQuietHours(start_hour=22, end_hour=7),
        ),
        activity=ReminderActivityInput(
            already_logged_recently=True,
            recent_activity_detected=True,
        ),
        context=_context(23, 15),
    )

    assert decision.decision == "suppress"
    assert decision.reasonCodes == [
        "reminders_disabled",
        "quiet_hours",
        "already_logged_recently",
        "recent_activity_detected",
    ]


def test_frequency_cap_suppresses_when_daily_limit_reached() -> None:
    """When the daily send count >= DAILY_REMINDER_CAP, the engine must suppress."""
    state = _load_state_fixture()
    state.quality.mealsLogged = 1
    state.quality.dataCompletenessScore = 1.0

    decision = evaluate_reminder_decision(
        state=state,
        preferences=ReminderPreferencesInput(),
        activity=ReminderActivityInput(daily_send_count=3),
        context=_context(13, 0),
    )

    assert decision.decision == "suppress"
    assert decision.kind is None
    assert decision.reasonCodes == ["frequency_cap_reached"]
    assert decision.confidence == 1.0


def test_frequency_cap_does_not_suppress_below_limit() -> None:
    """Below the cap, send decisions must still work normally."""
    state = _load_state_fixture()
    state.quality.mealsLogged = 1
    state.quality.dataCompletenessScore = 1.0

    decision = evaluate_reminder_decision(
        state=state,
        preferences=ReminderPreferencesInput(),
        activity=ReminderActivityInput(daily_send_count=2),
        context=_context(13, 0),
    )

    assert decision.decision == "send"
    assert decision.kind == "log_next_meal"


def test_frequency_cap_ordering_with_other_suppressions() -> None:
    """Frequency cap appears in deterministic order alongside other suppressions."""
    state = _load_state_fixture()
    state.quality.mealsLogged = 1

    decision = evaluate_reminder_decision(
        state=state,
        preferences=ReminderPreferencesInput(
            reminders_enabled=False,
            quiet_hours=ReminderQuietHours(start_hour=22, end_hour=7),
        ),
        activity=ReminderActivityInput(
            already_logged_recently=True,
            recent_activity_detected=True,
            daily_send_count=5,
        ),
        context=_context(23, 15),
    )

    assert decision.decision == "suppress"
    assert decision.reasonCodes == [
        "reminders_disabled",
        "quiet_hours",
        "frequency_cap_reached",
        "already_logged_recently",
        "recent_activity_detected",
    ]


def test_frequency_cap_valid_until_is_end_of_day() -> None:
    """Frequency cap suppression should last until end of the local day."""
    state = _load_state_fixture()
    state.quality.mealsLogged = 1
    state.quality.dataCompletenessScore = 1.0

    decision = evaluate_reminder_decision(
        state=state,
        preferences=ReminderPreferencesInput(),
        activity=ReminderActivityInput(daily_send_count=3),
        context=_context(14, 0),
    )

    assert decision.decision == "suppress"
    assert decision.validUntil == "2026-03-18T23:59:59Z"


def test_canonical_utc_timestamps_strip_microseconds_for_send() -> None:
    """Regression: datetime.now(UTC) carries microseconds. All emitted timestamps
    must conform to YYYY-MM-DDTHH:MM:SSZ (exactly 20 chars, no sub-seconds)."""
    state = _load_state_fixture()
    state.quality.mealsLogged = 0
    state.quality.dataCompletenessScore = 1.0

    decision = evaluate_reminder_decision(
        state=state,
        preferences=ReminderPreferencesInput(
            first_meal_window=ReminderWindow(start_min=450, end_min=570)
        ),
        activity=ReminderActivityInput(),
        context=ReminderContextInput(
            now_local=datetime(2026, 3, 18, 8, 20, 45, 123456, tzinfo=UTC)
        ),
    )

    assert decision.decision == "send"
    assert decision.computedAt == "2026-03-18T08:20:45Z"
    assert len(decision.computedAt) == 20
    assert "." not in decision.computedAt
    assert decision.scheduledAtUtc is not None
    assert len(decision.scheduledAtUtc) == 20
    assert "." not in decision.scheduledAtUtc
    assert len(decision.validUntil) == 20
    assert "." not in decision.validUntil


def test_canonical_utc_timestamps_strip_microseconds_for_suppress() -> None:
    state = _load_state_fixture()
    state.quality.mealsLogged = 1

    decision = evaluate_reminder_decision(
        state=state,
        preferences=ReminderPreferencesInput(reminders_enabled=False),
        activity=ReminderActivityInput(),
        context=ReminderContextInput(
            now_local=datetime(2026, 3, 18, 13, 0, 33, 999999, tzinfo=UTC)
        ),
    )

    assert decision.decision == "suppress"
    assert decision.computedAt == "2026-03-18T13:00:33Z"
    assert len(decision.computedAt) == 20
    assert "." not in decision.computedAt
    assert len(decision.validUntil) == 20
    assert "." not in decision.validUntil


def test_canonical_utc_timestamps_strip_microseconds_for_noop() -> None:
    state = _load_state_fixture()

    decision = evaluate_reminder_decision(
        state=state,
        preferences=ReminderPreferencesInput(),
        activity=ReminderActivityInput(),
        context=ReminderContextInput(
            now_local=datetime(2026, 3, 18, 20, 0, 0, 500000, tzinfo=UTC)
        ),
    )

    assert decision.decision == "noop"
    assert decision.computedAt == "2026-03-18T20:00:00Z"
    assert len(decision.computedAt) == 20
    assert "." not in decision.computedAt
    assert len(decision.validUntil) == 20
    assert "." not in decision.validUntil
