from datetime import UTC, datetime

from app.services.reminder_engine.decisions import (
    evaluate_complete_day,
    evaluate_first_meal,
    evaluate_next_meal,
    is_day_already_complete,
    is_day_empty,
    is_later_incomplete_day,
    is_partially_logged_day,
)
from app.services.reminder_engine.types import (
    LOW_ENGAGEMENT_TIMING_POLICY,
    NEUTRAL_TIMING_POLICY,
    ReminderPreferencesInput,
    ReminderWindow,
)
from tests.reminder_engine._helpers import load_state_fixture


def test_day_state_predicates_are_consistent() -> None:
    empty_state = load_state_fixture()
    empty_state.quality.mealsLogged = 0
    assert is_day_empty(empty_state) is True
    assert is_partially_logged_day(empty_state) is False

    complete_state = load_state_fixture()
    assert is_day_already_complete(complete_state) is True
    assert is_partially_logged_day(complete_state) is False

    partial_state = load_state_fixture()
    partial_state.quality.mealsLogged = 1
    partial_state.quality.dataCompletenessScore = 0.8
    assert is_day_empty(partial_state) is False
    assert is_day_already_complete(partial_state) is False
    assert is_partially_logged_day(partial_state) is True
    assert is_later_incomplete_day(partial_state, datetime(2026, 3, 18, 19, 45, tzinfo=UTC)) is True


def test_evaluate_first_meal_returns_noop_when_signal_is_insufficient() -> None:
    state = load_state_fixture()
    state.quality.mealsLogged = 0
    state.quality.dataCompletenessScore = 0.2
    state.habits.behavior.validLoggingDays7 = 1
    state.habits.behavior.dayCoverage14.validLoggedDays = 1
    state.habits.behavior.timingPatterns14.available = False
    state.habits.behavior.timingPatterns14.observedDays = 0

    decision = evaluate_first_meal(
        state,
        ReminderPreferencesInput(first_meal_window=ReminderWindow(start_min=450, end_min=570)),
        datetime(2026, 3, 18, 8, 20, tzinfo=UTC),
        "2026-03-18T08:20:00Z",
        NEUTRAL_TIMING_POLICY,
    )

    assert decision.decision == "noop"
    assert decision.kind is None
    assert decision.reasonCodes == ["insufficient_signal"]


def test_evaluate_next_meal_returns_none_without_signal() -> None:
    state = load_state_fixture()
    state.quality.mealsLogged = 1
    state.quality.dataCompletenessScore = 0.2
    state.habits.behavior.timingPatterns14.available = False
    state.habits.behavior.timingPatterns14.observedDays = 0

    decision = evaluate_next_meal(
        state,
        ReminderPreferencesInput(),
        datetime(2026, 3, 18, 13, 0, tzinfo=UTC),
        "2026-03-18T13:00:00Z",
        NEUTRAL_TIMING_POLICY,
    )

    assert decision is None


def test_evaluate_complete_day_returns_none_when_guarded_threshold_not_met() -> None:
    state = load_state_fixture()
    state.quality.mealsLogged = 2
    state.quality.dataCompletenessScore = 0.8
    state.habits.behavior.avgValidMealsPerValidLoggedDay14 = 3.4

    decision = evaluate_complete_day(
        state,
        ReminderPreferencesInput(complete_day_window=ReminderWindow(start_min=1080, end_min=1320)),
        datetime(2026, 3, 18, 19, 45, tzinfo=UTC),
        "2026-03-18T19:45:00Z",
        LOW_ENGAGEMENT_TIMING_POLICY,
    )

    assert decision is None


def test_evaluate_complete_day_sends_when_signal_is_sufficient() -> None:
    state = load_state_fixture()
    state.quality.mealsLogged = 2
    state.quality.dataCompletenessScore = 0.8

    decision = evaluate_complete_day(
        state,
        ReminderPreferencesInput(complete_day_window=ReminderWindow(start_min=1080, end_min=1320)),
        datetime(2026, 3, 18, 19, 45, tzinfo=UTC),
        "2026-03-18T19:45:00Z",
        NEUTRAL_TIMING_POLICY,
    )

    assert decision is not None
    assert decision.decision == "send"
    assert decision.kind == "complete_day"
