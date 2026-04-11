import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from app.schemas.nutrition_state import NutritionStateResponse
from app.services.reminder_engine.types import (
    ReminderActivityInput,
    ReminderContextInput,
    ReminderPreferencesInput,
    ReminderQuietHours,
    ReminderWindow,
)
from app.services.reminder_rule_engine import (
    evaluate_reminder_decision,
)

FIXTURES_DIR = Path(__file__).parent / "contract_fixtures"


def _load_state_fixture() -> NutritionStateResponse:
    payload = json.loads((FIXTURES_DIR / "nutrition_state.json").read_text(encoding="utf-8"))
    return NutritionStateResponse.model_validate(payload)


def _context(hour: int, minute: int) -> ReminderContextInput:
    return ReminderContextInput(now_local=datetime(2026, 3, 18, hour, minute, tzinfo=UTC))


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


# ---------------------------------------------------------------------------
# Smarter Timing v1.5 — send-now vs defer heuristics
# ---------------------------------------------------------------------------


def test_defer_when_preferred_open_but_far_from_habit_anchor() -> None:
    """Strong habit signal + far from anchor → defer to habit anchor inside
    the preferred window instead of sending immediately."""
    state = _load_state_fixture()
    state.quality.mealsLogged = 0
    state.quality.dataCompletenessScore = 1.0
    # observedDays=8 from fixture → strong signal
    # firstMealMedianHour=8.25 → anchor at 495 min (8:15)

    decision = evaluate_reminder_decision(
        state=state,
        preferences=ReminderPreferencesInput(
            # Wide window 6:00–11:00 that contains the habit anchor
            first_meal_window=ReminderWindow(start_min=360, end_min=660)
        ),
        activity=ReminderActivityInput(),
        # 6:20 = 380 min — inside preferred window but before habit window (405–585)
        context=_context(6, 20),
    )

    assert decision.decision == "send"
    assert decision.kind == "log_first_meal"
    # Deferred to habit anchor 8:15, NOT immediate at 6:20
    assert decision.scheduledAtUtc == "2026-03-18T08:15:00Z"
    assert "preferred_window_open" in decision.reasonCodes
    assert "habit_window_today" in decision.reasonCodes


def test_send_now_when_preferred_open_and_near_habit_anchor() -> None:
    """When current time is inside both preferred and habit windows,
    send immediately with merged reason codes."""
    state = _load_state_fixture()
    state.quality.mealsLogged = 0
    state.quality.dataCompletenessScore = 1.0
    # anchor at 495 min (8:15), habit window 405–585

    decision = evaluate_reminder_decision(
        state=state,
        preferences=ReminderPreferencesInput(
            first_meal_window=ReminderWindow(start_min=360, end_min=660)
        ),
        activity=ReminderActivityInput(),
        # 8:10 = 490 min — inside both windows, 5 min from anchor
        context=_context(8, 10),
    )

    assert decision.decision == "send"
    assert decision.kind == "log_first_meal"
    assert decision.scheduledAtUtc == "2026-03-18T08:10:00Z"
    assert decision.reasonCodes == [
        "preferred_window_open",
        "habit_window_match",
        "day_empty",
        "logging_usually_happens_now",
    ]


def test_wide_preference_defers_to_habit_anchor() -> None:
    """Very wide preferred window + strong habit → schedule at habit anchor,
    not at current time despite window being open."""
    state = _load_state_fixture()
    state.quality.mealsLogged = 0
    state.quality.dataCompletenessScore = 1.0
    # anchor at 495 (8:15)

    decision = evaluate_reminder_decision(
        state=state,
        preferences=ReminderPreferencesInput(
            # Ultra-wide window 5:00–12:00
            first_meal_window=ReminderWindow(start_min=300, end_min=720)
        ),
        activity=ReminderActivityInput(),
        # 5:30 = 330 min — far from anchor (165 min)
        context=_context(5, 30),
    )

    assert decision.decision == "send"
    assert decision.kind == "log_first_meal"
    assert decision.scheduledAtUtc == "2026-03-18T08:15:00Z"
    assert "preferred_window_open" in decision.reasonCodes
    assert "habit_window_today" in decision.reasonCodes


def test_habit_outside_preference_bounds_not_used() -> None:
    """When habit anchor falls outside preference window bounds,
    it must not influence timing — preference-only path applies."""
    state = _load_state_fixture()
    state.quality.mealsLogged = 0
    state.quality.dataCompletenessScore = 1.0
    # firstMealMedianHour=8.25 → anchor 495 — outside 600–720

    decision = evaluate_reminder_decision(
        state=state,
        preferences=ReminderPreferencesInput(
            # 10:00–12:00 — habit anchor at 8:15 is outside
            first_meal_window=ReminderWindow(start_min=600, end_min=720)
        ),
        activity=ReminderActivityInput(),
        # 7:30 = 450 — before preferred window, inside habit window (405–585)
        context=_context(7, 30),
    )

    assert decision.decision == "send"
    assert decision.kind == "log_first_meal"
    # Deferred to preferred window start, habit is bounded out
    assert decision.scheduledAtUtc == "2026-03-18T10:00:00Z"
    assert "preferred_window_today" in decision.reasonCodes
    assert "habit_window_match" not in decision.reasonCodes
    assert "logging_usually_happens_now" not in decision.reasonCodes


def test_weak_habit_signal_prefers_preference_timing() -> None:
    """With few observed days, the engine should trust preference timing
    and send immediately when the window is open — not defer to habit."""
    state = _load_state_fixture()
    state.quality.mealsLogged = 0
    state.quality.dataCompletenessScore = 1.0
    state.habits.behavior.timingPatterns14.observedDays = 4  # weak (<7)

    decision = evaluate_reminder_decision(
        state=state,
        preferences=ReminderPreferencesInput(
            first_meal_window=ReminderWindow(start_min=360, end_min=660)
        ),
        activity=ReminderActivityInput(),
        # 6:20 = 380 — same scenario as defer test, but weak signal
        context=_context(6, 20),
    )

    assert decision.decision == "send"
    assert decision.kind == "log_first_meal"
    # Weak signal → send now, not deferred to anchor
    assert decision.scheduledAtUtc == "2026-03-18T06:20:00Z"
    assert "preferred_window_open" in decision.reasonCodes


def test_deferred_preference_shifts_to_habit_anchor() -> None:
    """When the preferred window hasn't opened yet but a strong habit anchor
    falls INSIDE the window and later than window start, schedule at the
    habit anchor instead of the window start."""
    state = _load_state_fixture()
    state.quality.mealsLogged = 0
    state.quality.dataCompletenessScore = 1.0
    # firstMealMedianHour=8.25 → anchor 495 (8:15)

    decision = evaluate_reminder_decision(
        state=state,
        preferences=ReminderPreferencesInput(
            # 8:00–10:00 — habit anchor 8:15 is inside, later than start
            first_meal_window=ReminderWindow(start_min=480, end_min=600)
        ),
        activity=ReminderActivityInput(),
        # 7:30 = 450 — before preferred window
        context=_context(7, 30),
    )

    assert decision.decision == "send"
    assert decision.kind == "log_first_meal"
    # Deferred to habit anchor 8:15, NOT preferred start 8:00
    assert decision.scheduledAtUtc == "2026-03-18T08:15:00Z"
    assert "preferred_window_today" in decision.reasonCodes
    assert "habit_window_today" in decision.reasonCodes


def test_next_meal_defers_to_habit_with_preference_window() -> None:
    """Smarter timing heuristics apply to log_next_meal the same way they
    do for log_first_meal — verify defer works for partially logged day."""
    state = _load_state_fixture()
    state.quality.mealsLogged = 1
    state.quality.dataCompletenessScore = 1.0
    # lunchMedianHour=13.0 → anchor 780 (13:00)

    decision = evaluate_reminder_decision(
        state=state,
        preferences=ReminderPreferencesInput(
            # Wide window 11:00–15:00 containing lunch anchor
            next_meal_window=ReminderWindow(start_min=660, end_min=900)
        ),
        activity=ReminderActivityInput(),
        # 11:20 = 680 — in preferred window, before habit window (690–870)
        context=_context(11, 20),
    )

    assert decision.decision == "send"
    assert decision.kind == "log_next_meal"
    # Deferred to lunch anchor 13:00, NOT immediate at 11:20
    assert decision.scheduledAtUtc == "2026-03-18T13:00:00Z"
    assert "preferred_window_open" in decision.reasonCodes
    assert "habit_window_today" in decision.reasonCodes


def test_observed_days_at_strong_threshold_defers() -> None:
    """observedDays=7 is the exact boundary for strong signal.
    At this threshold, defer should still apply."""
    state = _load_state_fixture()
    state.quality.mealsLogged = 0
    state.quality.dataCompletenessScore = 1.0
    state.habits.behavior.timingPatterns14.observedDays = 7  # exact boundary

    decision = evaluate_reminder_decision(
        state=state,
        preferences=ReminderPreferencesInput(
            first_meal_window=ReminderWindow(start_min=360, end_min=660)
        ),
        activity=ReminderActivityInput(),
        context=_context(6, 20),
    )

    assert decision.decision == "send"
    assert decision.kind == "log_first_meal"
    # Strong signal at boundary → defer to anchor 8:15
    assert decision.scheduledAtUtc == "2026-03-18T08:15:00Z"
    assert "habit_window_today" in decision.reasonCodes


# ---------------------------------------------------------------------------
# Smarter Timing v1.5 — complete_day refinement
# ---------------------------------------------------------------------------


def test_complete_day_not_scheduled_before_buffer() -> None:
    """complete_day must not fire before lastMealMedianHour + buffer.
    At 19:00 (= lastMealMedian), the day likely still has an ongoing meal.
    Should fall through to log_next_meal instead."""
    state = _load_state_fixture()
    state.quality.mealsLogged = 2
    state.quality.dataCompletenessScore = 0.8

    decision = evaluate_reminder_decision(
        state=state,
        preferences=ReminderPreferencesInput(
            complete_day_window=ReminderWindow(start_min=1080, end_min=1260)
        ),
        activity=ReminderActivityInput(),
        # 19:00 = 1140 — at lastMealMedian, before buffer (1170)
        context=_context(19, 0),
    )

    assert decision.decision == "send"
    assert decision.kind == "log_next_meal"
    assert decision.scheduledAtUtc == "2026-03-18T19:00:00Z"
    assert decision.reasonCodes == [
        "habit_window_match",
        "day_partially_logged",
        "logging_usually_happens_now",
    ]


def test_complete_day_sends_after_buffer() -> None:
    """Once past lastMealMedian + buffer, complete_day should fire normally."""
    state = _load_state_fixture()
    state.quality.mealsLogged = 2
    state.quality.dataCompletenessScore = 0.8

    decision = evaluate_reminder_decision(
        state=state,
        preferences=ReminderPreferencesInput(
            complete_day_window=ReminderWindow(start_min=1080, end_min=1320)
        ),
        activity=ReminderActivityInput(),
        # 19:45 = 1185 — past buffer (1170)
        context=_context(19, 45),
    )

    assert decision.decision == "send"
    assert decision.kind == "complete_day"


def test_complete_day_weak_signal_no_send() -> None:
    """With too few observed days, the lastMealMedianHour is unreliable.
    complete_day should not be sent."""
    state = _load_state_fixture()
    state.quality.mealsLogged = 2
    state.quality.dataCompletenessScore = 0.8
    state.habits.behavior.timingPatterns14.observedDays = 3  # below threshold (4)

    decision = evaluate_reminder_decision(
        state=state,
        preferences=ReminderPreferencesInput(
            complete_day_window=ReminderWindow(start_min=1080, end_min=1260)
        ),
        activity=ReminderActivityInput(),
        context=_context(20, 0),
    )

    assert decision.decision == "send"
    assert decision.kind == "log_next_meal"
    assert decision.scheduledAtUtc == "2026-03-18T20:00:00Z"
    assert decision.reasonCodes == [
        "habit_window_match",
        "day_partially_logged",
        "logging_usually_happens_now",
    ]


def test_complete_day_respects_preference_bounds() -> None:
    """When the preference window starts later than the habit anchor,
    complete_day must defer to the preference window start."""
    state = _load_state_fixture()
    state.quality.mealsLogged = 2
    state.quality.dataCompletenessScore = 0.8

    decision = evaluate_reminder_decision(
        state=state,
        preferences=ReminderPreferencesInput(
            # Preference window starts at 20:30 — after habit anchor (19:30)
            complete_day_window=ReminderWindow(start_min=1230, end_min=1350)
        ),
        activity=ReminderActivityInput(),
        # 19:45 = 1185 — past buffer, but before preference window
        context=_context(19, 45),
    )

    assert decision.decision == "send"
    assert decision.kind == "complete_day"
    assert decision.scheduledAtUtc == "2026-03-18T20:30:00Z"
    assert decision.reasonCodes == [
        "preferred_window_today",
        "day_partially_logged",
    ]


# ---------------------------------------------------------------------------
# Hardening — preference hard bounds, quiet hours revalidation, tighter overlap
# ---------------------------------------------------------------------------


def test_habit_blocked_after_preferred_window_passed() -> None:
    """When a preferred window has passed, habit-only candidates must not
    produce a send decision — preferences are hard bounds."""
    state = _load_state_fixture()
    state.quality.mealsLogged = 0
    state.quality.dataCompletenessScore = 1.0
    # firstMealMedianHour=8.25 → anchor 495, habit window [405, 585]

    decision = evaluate_reminder_decision(
        state=state,
        preferences=ReminderPreferencesInput(
            # 7:00–9:00 window — already passed by 9:30
            first_meal_window=ReminderWindow(start_min=420, end_min=540)
        ),
        activity=ReminderActivityInput(),
        # 9:30 = 570 — preferred window passed, but inside habit window [405, 585]
        context=_context(9, 30),
    )

    assert decision.decision == "noop"
    assert decision.kind is None
    assert decision.scheduledAtUtc is None
    assert decision.reasonCodes == ["insufficient_signal"]


def test_deferred_schedule_blocked_by_quiet_hours() -> None:
    """A deferred schedule that would land inside quiet hours must not be sent,
    even if the current time is outside quiet hours."""
    state = _load_state_fixture()
    state.quality.mealsLogged = 0
    state.quality.dataCompletenessScore = 1.0
    # firstMealMedianHour=8.25 → anchor 495 (8:15), strong signal (observedDays=8)

    decision = evaluate_reminder_decision(
        state=state,
        preferences=ReminderPreferencesInput(
            # Wide preferred window 6:00–11:00
            first_meal_window=ReminderWindow(start_min=360, end_min=660),
            # Morning focus: quiet hours 8:00–10:00
            quiet_hours=ReminderQuietHours(start_hour=8, end_hour=10),
        ),
        activity=ReminderActivityInput(),
        # 7:00 = 420 — not in quiet hours, preferred window open
        context=_context(7, 0),
    )

    assert decision.decision == "noop"
    assert decision.kind is None
    assert decision.scheduledAtUtc is None
    assert decision.reasonCodes == ["insufficient_signal"]


def test_inside_broad_habit_overlap_but_far_from_anchor_defers() -> None:
    """Inside the broad habit area is not enough for send-now when the anchor
    is still materially ahead and preference overlap is wide."""
    state = _load_state_fixture()
    state.quality.mealsLogged = 0
    state.quality.dataCompletenessScore = 1.0

    decision = evaluate_reminder_decision(
        state=state,
        preferences=ReminderPreferencesInput(
            first_meal_window=ReminderWindow(start_min=360, end_min=660)
        ),
        activity=ReminderActivityInput(),
        # 6:50 = 410 — inside habit window [405, 585], but 85 min before 8:15 anchor
        context=_context(6, 50),
    )

    assert decision.decision == "send"
    assert decision.kind == "log_first_meal"
    assert decision.scheduledAtUtc == "2026-03-18T08:15:00Z"
    assert decision.reasonCodes == [
        "preferred_window_open",
        "habit_window_today",
        "day_empty",
    ]


def test_competing_immediate_habit_anchors_closest_to_now_wins() -> None:
    """When multiple immediate anchors overlap, the nearest anchor should win."""
    state = _load_state_fixture()
    state.quality.mealsLogged = 1
    state.quality.dataCompletenessScore = 1.0
    state.habits.behavior.timingPatterns14.snackMedianHour = 13.25  # 13:15

    decision = evaluate_reminder_decision(
        state=state,
        preferences=ReminderPreferencesInput(),
        activity=ReminderActivityInput(),
        # lunch 13:00 and snack 13:15 are both immediate at 13:05
        context=_context(13, 5),
    )

    assert decision.decision == "send"
    assert decision.kind == "log_next_meal"
    assert decision.scheduledAtUtc == "2026-03-18T13:05:00Z"
    assert decision.reasonCodes == [
        "habit_window_match",
        "day_partially_logged",
        "logging_usually_happens_now",
    ]
    # Closest anchor is lunch at 13:00, so validUntil comes from lunch window end 14:30.
    assert decision.validUntil == "2026-03-18T14:30:00Z"


def test_immediate_habit_anchor_beats_deferred_one() -> None:
    """An immediate candidate should win over another anchor that is still
    deferred in the future."""
    state = _load_state_fixture()
    state.quality.mealsLogged = 1
    state.quality.dataCompletenessScore = 1.0
    state.habits.behavior.timingPatterns14.snackMedianHour = 14.0

    decision = evaluate_reminder_decision(
        state=state,
        preferences=ReminderPreferencesInput(),
        activity=ReminderActivityInput(),
        # lunch 13:00 is immediate at 13:05; snack 14:00 stays deferred
        context=_context(13, 5),
    )

    assert decision.decision == "send"
    assert decision.kind == "log_next_meal"
    assert decision.scheduledAtUtc == "2026-03-18T13:05:00Z"
    assert decision.reasonCodes == [
        "habit_window_match",
        "day_partially_logged",
        "logging_usually_happens_now",
    ]
    assert decision.validUntil == "2026-03-18T14:30:00Z"


def test_competing_immediate_habit_anchors_tie_preserves_deterministic_order() -> None:
    """Equal-distance immediate anchors should resolve deterministically using
    the stable candidate order from _candidate_habit_minutes."""
    state = _load_state_fixture()
    state.quality.mealsLogged = 1
    state.quality.dataCompletenessScore = 1.0
    state.habits.behavior.timingPatterns14.snackMedianHour = 13.3334  # 13:20 after int-minute conversion

    decision = evaluate_reminder_decision(
        state=state,
        preferences=ReminderPreferencesInput(),
        activity=ReminderActivityInput(),
        # 13:10 is equally distant from lunch 13:00 and snack 13:20.
        context=_context(13, 10),
    )

    assert decision.decision == "send"
    assert decision.kind == "log_next_meal"
    assert decision.scheduledAtUtc == "2026-03-18T13:10:00Z"
    assert decision.reasonCodes == [
        "habit_window_match",
        "day_partially_logged",
        "logging_usually_happens_now",
    ]
    # Lunch remains the selected anchor on the tie/near-tie boundary, so the
    # selected window still expires at lunch's 14:30 end.
    assert decision.validUntil == "2026-03-18T14:30:00Z"


def test_competing_deferred_habit_anchors_earliest_valid_deferred_wins() -> None:
    """When all viable habit anchors are deferred, the earliest future one wins."""
    state = _load_state_fixture()
    state.quality.mealsLogged = 1
    state.quality.dataCompletenessScore = 1.0
    state.habits.behavior.timingPatterns14.breakfastMedianHour = None
    state.habits.behavior.timingPatterns14.lunchMedianHour = 13.0
    state.habits.behavior.timingPatterns14.snackMedianHour = 15.0
    state.habits.behavior.timingPatterns14.dinnerMedianHour = 19.0
    state.habits.behavior.timingPatterns14.lastMealMedianHour = 19.0

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


def test_complete_day_fallback_respects_preference_bounds() -> None:
    """When complete_day is rejected (window passed), the engine falls through
    to log_next_meal.  The fallback path must also respect its own preference
    bounds — habit anchors outside next_meal_window must not influence timing."""
    state = _load_state_fixture()
    state.quality.mealsLogged = 2
    state.quality.dataCompletenessScore = 0.8

    decision = evaluate_reminder_decision(
        state=state,
        preferences=ReminderPreferencesInput(
            # complete_day window 18:00–19:00 — already passed at 20:00
            complete_day_window=ReminderWindow(start_min=1080, end_min=1140),
            # next_meal window 20:00–21:00 — constrains fallback
            next_meal_window=ReminderWindow(start_min=1200, end_min=1260),
        ),
        activity=ReminderActivityInput(),
        # 20:00 = 1200 — past complete_day window, inside next_meal window
        context=_context(20, 0),
    )

    assert decision.decision == "send"
    assert decision.kind == "log_next_meal"
    assert decision.scheduledAtUtc == "2026-03-18T20:00:00Z"
    assert decision.reasonCodes == [
        "preferred_window_open",
        "day_partially_logged",
    ]


def test_complete_day_not_sent_with_too_few_meals() -> None:
    """With only 1 meal logged out of expected 3, the day is still in progress.
    Should get log_next_meal instead of complete_day."""
    state = _load_state_fixture()
    state.quality.mealsLogged = 1
    state.quality.dataCompletenessScore = 0.5

    decision = evaluate_reminder_decision(
        state=state,
        preferences=ReminderPreferencesInput(
            complete_day_window=ReminderWindow(start_min=1080, end_min=1260)
        ),
        activity=ReminderActivityInput(),
        context=_context(20, 0),
    )

    assert decision.decision == "send"
    assert decision.kind == "log_next_meal"
    assert decision.scheduledAtUtc == "2026-03-18T20:00:00Z"
    assert decision.reasonCodes == [
        "habit_window_match",
        "day_partially_logged",
        "logging_usually_happens_now",
    ]


def test_responsive_profile_defers_with_lower_strong_signal_threshold() -> None:
    state = _load_state_fixture()
    state.quality.mealsLogged = 0
    state.quality.dataCompletenessScore = 1.0
    state.habits.behavior.validLoggingDays7 = 4
    state.habits.behavior.validLoggingConsistency28 = 0.55
    state.habits.behavior.dayCoverage14.validLoggedDays = 6
    state.habits.behavior.avgValidMealsPerValidLoggedDay14 = 2.6
    state.habits.behavior.timingPatterns14.observedDays = 6

    decision = evaluate_reminder_decision(
        state=state,
        preferences=ReminderPreferencesInput(
            first_meal_window=ReminderWindow(start_min=360, end_min=660)
        ),
        activity=ReminderActivityInput(),
        context=_context(6, 20),
    )

    assert decision.decision == "send"
    assert decision.kind == "log_first_meal"
    assert decision.scheduledAtUtc == "2026-03-18T08:15:00Z"
    assert decision.reasonCodes == [
        "preferred_window_open",
        "habit_window_today",
        "day_empty",
    ]


def test_neutral_profile_keeps_preference_timing_in_same_window() -> None:
    state = _load_state_fixture()
    state.quality.mealsLogged = 0
    state.quality.dataCompletenessScore = 1.0
    state.habits.behavior.validLoggingDays7 = 3
    state.habits.behavior.validLoggingConsistency28 = 0.4
    state.habits.behavior.dayCoverage14.validLoggedDays = 5
    state.habits.behavior.avgValidMealsPerValidLoggedDay14 = 2.3
    state.habits.behavior.timingPatterns14.observedDays = 6

    decision = evaluate_reminder_decision(
        state=state,
        preferences=ReminderPreferencesInput(
            first_meal_window=ReminderWindow(start_min=360, end_min=660)
        ),
        activity=ReminderActivityInput(),
        context=_context(6, 20),
    )

    assert decision.decision == "send"
    assert decision.kind == "log_first_meal"
    assert decision.scheduledAtUtc == "2026-03-18T06:20:00Z"
    assert decision.reasonCodes == [
        "preferred_window_open",
        "day_empty",
    ]


def test_responsive_profile_defers_when_inside_habit_window_but_25_minutes_early() -> None:
    state = _load_state_fixture()
    state.quality.mealsLogged = 0
    state.quality.dataCompletenessScore = 1.0
    state.habits.behavior.validLoggingDays7 = 4
    state.habits.behavior.validLoggingConsistency28 = 0.55
    state.habits.behavior.dayCoverage14.validLoggedDays = 6
    state.habits.behavior.avgValidMealsPerValidLoggedDay14 = 2.6
    state.habits.behavior.timingPatterns14.observedDays = 8

    decision = evaluate_reminder_decision(
        state=state,
        preferences=ReminderPreferencesInput(),
        activity=ReminderActivityInput(),
        context=_context(7, 50),
    )

    assert decision.decision == "send"
    assert decision.kind == "log_first_meal"
    assert decision.scheduledAtUtc == "2026-03-18T08:15:00Z"
    assert decision.reasonCodes == [
        "habit_window_today",
        "day_empty",
    ]


def test_self_sufficient_profile_sends_now_in_same_25_minute_habit_overlap() -> None:
    state = _load_state_fixture()
    state.quality.mealsLogged = 0
    state.quality.dataCompletenessScore = 1.0
    state.habits.behavior.validLoggingDays7 = 5
    state.habits.behavior.validLoggingConsistency28 = 0.82
    state.habits.behavior.dayCoverage14.validLoggedDays = 10
    state.habits.behavior.avgValidMealsPerValidLoggedDay14 = 3.4
    state.habits.behavior.timingPatterns14.observedDays = 8

    decision = evaluate_reminder_decision(
        state=state,
        preferences=ReminderPreferencesInput(),
        activity=ReminderActivityInput(),
        context=_context(7, 50),
    )

    assert decision.decision == "send"
    assert decision.kind == "log_first_meal"
    assert decision.scheduledAtUtc == "2026-03-18T07:50:00Z"
    assert decision.reasonCodes == [
        "habit_window_match",
        "day_empty",
        "logging_usually_happens_now",
    ]


def test_guarded_profiles_diverge_at_40_minute_anchor_boundary() -> None:
    low_engagement_state = _load_state_fixture()
    low_engagement_state.quality.mealsLogged = 0
    low_engagement_state.quality.dataCompletenessScore = 1.0
    low_engagement_state.habits.behavior.validLoggingDays7 = 2
    low_engagement_state.habits.behavior.validLoggingConsistency28 = 0.29
    low_engagement_state.habits.behavior.dayCoverage14.validLoggedDays = 3
    low_engagement_state.habits.behavior.avgValidMealsPerValidLoggedDay14 = 1.8
    low_engagement_state.habits.behavior.timingPatterns14.observedDays = 8

    self_sufficient_state = _load_state_fixture()
    self_sufficient_state.quality.mealsLogged = 0
    self_sufficient_state.quality.dataCompletenessScore = 1.0
    self_sufficient_state.habits.behavior.validLoggingDays7 = 5
    self_sufficient_state.habits.behavior.validLoggingConsistency28 = 0.82
    self_sufficient_state.habits.behavior.dayCoverage14.validLoggedDays = 10
    self_sufficient_state.habits.behavior.avgValidMealsPerValidLoggedDay14 = 3.4
    self_sufficient_state.habits.behavior.timingPatterns14.observedDays = 8

    low_engagement_decision = evaluate_reminder_decision(
        state=low_engagement_state,
        preferences=ReminderPreferencesInput(),
        activity=ReminderActivityInput(),
        context=_context(7, 35),
    )
    self_sufficient_decision = evaluate_reminder_decision(
        state=self_sufficient_state,
        preferences=ReminderPreferencesInput(),
        activity=ReminderActivityInput(),
        context=_context(7, 35),
    )

    assert low_engagement_decision.decision == "send"
    assert low_engagement_decision.kind == "log_first_meal"
    assert low_engagement_decision.scheduledAtUtc == "2026-03-18T07:35:00Z"
    assert low_engagement_decision.reasonCodes == [
        "habit_window_match",
        "day_empty",
        "logging_usually_happens_now",
    ]

    assert self_sufficient_decision.decision == "send"
    assert self_sufficient_decision.kind == "log_first_meal"
    assert self_sufficient_decision.scheduledAtUtc == "2026-03-18T08:15:00Z"
    assert self_sufficient_decision.reasonCodes == [
        "habit_window_today",
        "day_empty",
    ]


def test_responsive_profile_keeps_complete_day_after_buffer() -> None:
    state = _load_state_fixture()
    state.quality.mealsLogged = 2
    state.quality.dataCompletenessScore = 0.8
    state.habits.behavior.validLoggingDays7 = 4
    state.habits.behavior.validLoggingConsistency28 = 0.55
    state.habits.behavior.dayCoverage14.validLoggedDays = 6
    state.habits.behavior.avgValidMealsPerValidLoggedDay14 = 2.6
    state.habits.behavior.timingPatterns14.observedDays = 6

    decision = evaluate_reminder_decision(
        state=state,
        preferences=ReminderPreferencesInput(
            complete_day_window=ReminderWindow(start_min=1080, end_min=1320)
        ),
        activity=ReminderActivityInput(),
        context=_context(19, 45),
    )

    assert decision.decision == "send"
    assert decision.kind == "complete_day"
    assert decision.scheduledAtUtc == "2026-03-18T19:45:00Z"
    assert decision.reasonCodes == [
        "preferred_window_open",
        "habit_window_match",
        "day_partially_logged",
        "logging_usually_happens_now",
    ]


def test_low_engagement_profile_biases_complete_day_to_next_meal_fallback() -> None:
    state = _load_state_fixture()
    state.quality.mealsLogged = 2
    state.quality.dataCompletenessScore = 0.8
    state.habits.behavior.validLoggingDays7 = 2
    state.habits.behavior.validLoggingConsistency28 = 0.4
    state.habits.behavior.dayCoverage14.validLoggedDays = 4
    state.habits.behavior.avgValidMealsPerValidLoggedDay14 = 2.5
    state.habits.behavior.timingPatterns14.observedDays = 8

    decision = evaluate_reminder_decision(
        state=state,
        preferences=ReminderPreferencesInput(
            complete_day_window=ReminderWindow(start_min=1080, end_min=1320)
        ),
        activity=ReminderActivityInput(),
        context=_context(19, 45),
    )

    assert decision.decision == "send"
    assert decision.kind == "log_next_meal"
    assert decision.scheduledAtUtc == "2026-03-18T19:45:00Z"
    assert decision.reasonCodes == [
        "habit_window_match",
        "day_partially_logged",
        "logging_usually_happens_now",
    ]


def test_self_sufficient_profile_biases_complete_day_to_next_meal_fallback() -> None:
    state = _load_state_fixture()
    state.quality.mealsLogged = 3
    state.quality.dataCompletenessScore = 0.85
    state.habits.behavior.validLoggingDays7 = 5
    state.habits.behavior.validLoggingConsistency28 = 0.82
    state.habits.behavior.dayCoverage14.validLoggedDays = 10
    state.habits.behavior.avgValidMealsPerValidLoggedDay14 = 3.4
    state.habits.behavior.timingPatterns14.observedDays = 8

    decision = evaluate_reminder_decision(
        state=state,
        preferences=ReminderPreferencesInput(
            complete_day_window=ReminderWindow(start_min=1080, end_min=1320)
        ),
        activity=ReminderActivityInput(),
        context=_context(19, 45),
    )

    assert decision.decision == "send"
    assert decision.kind == "log_next_meal"
    assert decision.scheduledAtUtc == "2026-03-18T19:45:00Z"
    assert decision.reasonCodes == [
        "habit_window_match",
        "day_partially_logged",
        "logging_usually_happens_now",
    ]
