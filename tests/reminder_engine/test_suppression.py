from datetime import UTC, datetime

import pytest

from app.services.reminder_engine.suppression import evaluate_suppression
from app.services.reminder_engine.types import (
    ReminderActivityInput,
    ReminderContextInput,
    ReminderPreferencesInput,
    ReminderQuietHours,
)
from tests.reminder_engine._helpers import context


@pytest.mark.parametrize(
    ("preferences", "activity", "expected_reason_codes", "ctx"),
    [
        (
            ReminderPreferencesInput(reminders_enabled=False),
            ReminderActivityInput(),
            ["reminders_disabled"],
            context(13, 0),
        ),
        (
            ReminderPreferencesInput(
                quiet_hours=ReminderQuietHours(start_hour=22, end_hour=7)
            ),
            ReminderActivityInput(),
            ["quiet_hours"],
            context(23, 0),
        ),
        (
            ReminderPreferencesInput(),
            ReminderActivityInput(already_logged_recently=True),
            ["already_logged_recently"],
            context(13, 0),
        ),
        (
            ReminderPreferencesInput(),
            ReminderActivityInput(recent_activity_detected=True),
            ["recent_activity_detected"],
            context(13, 0),
        ),
        (
            ReminderPreferencesInput(),
            ReminderActivityInput(daily_send_count=3),
            ["frequency_cap_reached"],
            context(13, 0),
        ),
    ],
)
def test_hard_suppressions_take_precedence(
    preferences: ReminderPreferencesInput,
    activity: ReminderActivityInput,
    expected_reason_codes: list[str],
    ctx: ReminderContextInput,
) -> None:
    decision = evaluate_suppression(preferences, activity, ctx)

    assert decision is not None
    assert decision.decision == "suppress"
    assert decision.kind is None
    assert decision.reasonCodes == expected_reason_codes
    assert decision.confidence == 1.0


def test_reason_codes_are_deterministic_for_combined_suppressions() -> None:
    decision = evaluate_suppression(
        ReminderPreferencesInput(
            reminders_enabled=False,
            quiet_hours=ReminderQuietHours(start_hour=22, end_hour=7),
        ),
        ReminderActivityInput(
            already_logged_recently=True,
            recent_activity_detected=True,
            daily_send_count=5,
        ),
        context(23, 15),
    )

    assert decision is not None
    assert decision.reasonCodes == [
        "reminders_disabled",
        "quiet_hours",
        "frequency_cap_reached",
        "already_logged_recently",
        "recent_activity_detected",
    ]


def test_frequency_cap_does_not_suppress_below_limit() -> None:
    decision = evaluate_suppression(
        ReminderPreferencesInput(),
        ReminderActivityInput(daily_send_count=2),
        context(13, 0),
    )

    assert decision is None


def test_frequency_cap_valid_until_is_end_of_day() -> None:
    decision = evaluate_suppression(
        ReminderPreferencesInput(),
        ReminderActivityInput(daily_send_count=3),
        context(14, 0),
    )

    assert decision is not None
    assert decision.decision == "suppress"
    assert decision.validUntil == "2026-03-18T23:59:59Z"


def test_canonical_utc_timestamps_strip_microseconds_for_suppress() -> None:
    decision = evaluate_suppression(
        ReminderPreferencesInput(reminders_enabled=False),
        ReminderActivityInput(),
        ReminderContextInput(
            now_local=datetime(2026, 3, 18, 13, 0, 33, 999999, tzinfo=UTC)
        ),
    )

    assert decision is not None
    assert decision.decision == "suppress"
    assert decision.computedAt == "2026-03-18T13:00:33Z"
    assert len(decision.computedAt) == 20
    assert "." not in decision.computedAt
    assert len(decision.validUntil) == 20
    assert "." not in decision.validUntil
