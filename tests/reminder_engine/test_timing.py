from datetime import UTC, datetime

from app.services.reminder_engine.timing import _evaluate_window_plan, _select_best_habit
from app.services.reminder_engine.types import (
    NEUTRAL_TIMING_POLICY,
    ReminderQuietHours,
    ReminderWindow,
    _TimingCandidate,
)


def test_window_plan_merges_preferred_and_habit_immediate() -> None:
    now_local = datetime(2026, 3, 18, 8, 10, tzinfo=UTC)
    evaluation = _evaluate_window_plan(
        now_local=now_local,
        current_min=490,
        preferred_window=ReminderWindow(start_min=360, end_min=660),
        habit_minutes=(495,),
        radius_min=90,
        day_reason="day_empty",
        observed_days=8,
        timing_policy=NEUTRAL_TIMING_POLICY,
    )

    assert evaluation is not None
    assert evaluation.scheduled_at_local == now_local
    assert evaluation.reason_codes == [
        "preferred_window_open",
        "habit_window_match",
        "day_empty",
        "logging_usually_happens_now",
    ]


def test_window_plan_defers_to_habit_anchor_for_strong_signal() -> None:
    evaluation = _evaluate_window_plan(
        now_local=datetime(2026, 3, 18, 6, 20, tzinfo=UTC),
        current_min=380,
        preferred_window=ReminderWindow(start_min=360, end_min=660),
        habit_minutes=(495,),
        radius_min=90,
        day_reason="day_empty",
        observed_days=8,
        timing_policy=NEUTRAL_TIMING_POLICY,
    )

    assert evaluation is not None
    assert evaluation.scheduled_at_local == datetime(2026, 3, 18, 8, 15, tzinfo=UTC)
    assert evaluation.reason_codes == [
        "preferred_window_open",
        "habit_window_today",
        "day_empty",
    ]


def test_window_plan_returns_none_when_deferred_time_hits_quiet_hours() -> None:
    evaluation = _evaluate_window_plan(
        now_local=datetime(2026, 3, 18, 7, 0, tzinfo=UTC),
        current_min=420,
        preferred_window=ReminderWindow(start_min=360, end_min=660),
        habit_minutes=(495,),
        radius_min=90,
        day_reason="day_empty",
        observed_days=8,
        timing_policy=NEUTRAL_TIMING_POLICY,
        quiet_hours=ReminderQuietHours(start_hour=8, end_hour=10),
    )

    assert evaluation is None


def test_select_best_habit_prefers_closest_immediate_anchor() -> None:
    now_local = datetime(2026, 3, 18, 13, 5, tzinfo=UTC)
    habits = [
        _TimingCandidate(
            source="habit",
            scheduled_at_local=now_local,
            valid_until_local=datetime(2026, 3, 18, 14, 30, tzinfo=UTC),
            anchor_min=780,
            is_immediate=True,
            reason_codes=["day_partially_logged", "habit_window_match"],
        ),
        _TimingCandidate(
            source="habit",
            scheduled_at_local=now_local,
            valid_until_local=datetime(2026, 3, 18, 14, 45, tzinfo=UTC),
            anchor_min=795,
            is_immediate=True,
            reason_codes=["day_partially_logged", "habit_window_match"],
        ),
    ]

    selected = _select_best_habit(habits, current_min=785)

    assert selected is not None
    assert selected.anchor_min == 780
