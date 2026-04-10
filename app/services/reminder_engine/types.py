from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from app.schemas.reminders import ReminderReasonCode

UTC = timezone.utc

FIRST_MEAL_WINDOW_RADIUS_MIN = 90
NEXT_MEAL_WINDOW_RADIUS_MIN = 90
COMPLETE_DAY_WINDOW_RADIUS_MIN = 120
RECENT_ACTIVITY_SUPPRESSION_MIN = 90
LATEST_COMPLETE_DAY_START_MIN = 18 * 60
DAILY_REMINDER_CAP = 3
COMPLETE_DAY_BUFFER_MIN = 30

RESPONSIVE_PROFILE_MIN_OBSERVED_DAYS = 6
RESPONSIVE_PROFILE_MIN_VALID_LOGGING_DAYS7 = 4
RESPONSIVE_PROFILE_MIN_CONSISTENCY28 = 0.45
SELF_SUFFICIENT_PROFILE_MIN_CONSISTENCY28 = 0.7
SELF_SUFFICIENT_PROFILE_MIN_VALID_DAYS14 = 8
SELF_SUFFICIENT_PROFILE_MIN_AVG_MEALS14 = 3.0
LOW_ENGAGEMENT_PROFILE_MAX_VALID_LOGGING_DAYS7 = 2
LOW_ENGAGEMENT_PROFILE_MAX_CONSISTENCY28_EXCLUSIVE = 0.3
LOW_ENGAGEMENT_PROFILE_MAX_VALID_DAYS14 = 3
SELF_SUFFICIENT_DIRECT_TIMING_ANCHOR_PROXIMITY_MIN = 35
LOW_ENGAGEMENT_DIRECT_TIMING_ANCHOR_PROXIMITY_MIN = 45

REASON_CODE_ORDER: tuple[ReminderReasonCode, ...] = (
    "reminders_disabled",
    "quiet_hours",
    "frequency_cap_reached",
    "already_logged_recently",
    "recent_activity_detected",
    "preferred_window_open",
    "preferred_window_today",
    "habit_window_match",
    "habit_window_today",
    "day_empty",
    "day_partially_logged",
    "logging_usually_happens_now",
    "insufficient_signal",
    "day_already_complete",
)


@dataclass(frozen=True)
class ReminderWindow:
    start_min: int
    end_min: int


@dataclass(frozen=True)
class ReminderQuietHours:
    start_hour: int
    end_hour: int


@dataclass(frozen=True)
class ReminderPreferencesInput:
    reminders_enabled: bool = True
    quiet_hours: ReminderQuietHours | None = None
    first_meal_window: ReminderWindow | None = None
    next_meal_window: ReminderWindow | None = None
    complete_day_window: ReminderWindow | None = None


@dataclass(frozen=True)
class ReminderActivityInput:
    recent_activity_detected: bool = False
    already_logged_recently: bool = False
    daily_send_count: int = 0


@dataclass(frozen=True)
class ReminderContextInput:
    now_local: datetime


@dataclass(frozen=True)
class _WindowEvaluation:
    reason_codes: list[ReminderReasonCode]
    scheduled_at_local: datetime
    valid_until_local: datetime


_CandidateSource = Literal["preferred", "habit"]
_PersonalizationSegment = Literal[
    "responsive",
    "neutral",
    "low_engagement",
    "self_sufficient",
]


@dataclass(frozen=True)
class _TimingCandidate:
    source: _CandidateSource
    scheduled_at_local: datetime
    valid_until_local: datetime
    anchor_min: int
    is_immediate: bool
    reason_codes: list[ReminderReasonCode]


@dataclass(frozen=True)
class ReminderPersonalizationProfile:
    """Bounded per-user reminder profile for future timing/kind biasing.

    The segment is derived deterministically from existing habit/logging
    signals. It does not represent reminder-response telemetry.
    """

    segment: _PersonalizationSegment


@dataclass(frozen=True)
class ReminderTimingPolicy:
    """Small bounded knobs that a personalization segment may influence."""

    strong_habit_observed_days_min: int
    anchor_proximity_min: int
    prefer_anchor_inside_window: bool
    complete_day_guarded: bool


RESPONSIVE_TIMING_POLICY = ReminderTimingPolicy(
    strong_habit_observed_days_min=6,
    anchor_proximity_min=20,
    prefer_anchor_inside_window=True,
    complete_day_guarded=False,
)
NEUTRAL_TIMING_POLICY = ReminderTimingPolicy(
    strong_habit_observed_days_min=7,
    anchor_proximity_min=30,
    prefer_anchor_inside_window=True,
    complete_day_guarded=False,
)
SELF_SUFFICIENT_TIMING_POLICY = ReminderTimingPolicy(
    strong_habit_observed_days_min=8,
    anchor_proximity_min=SELF_SUFFICIENT_DIRECT_TIMING_ANCHOR_PROXIMITY_MIN,
    prefer_anchor_inside_window=False,
    complete_day_guarded=True,
)
LOW_ENGAGEMENT_TIMING_POLICY = ReminderTimingPolicy(
    strong_habit_observed_days_min=8,
    anchor_proximity_min=LOW_ENGAGEMENT_DIRECT_TIMING_ANCHOR_PROXIMITY_MIN,
    prefer_anchor_inside_window=False,
    complete_day_guarded=True,
)


__all__ = [
    "UTC",
    "FIRST_MEAL_WINDOW_RADIUS_MIN",
    "NEXT_MEAL_WINDOW_RADIUS_MIN",
    "COMPLETE_DAY_WINDOW_RADIUS_MIN",
    "RECENT_ACTIVITY_SUPPRESSION_MIN",
    "LATEST_COMPLETE_DAY_START_MIN",
    "DAILY_REMINDER_CAP",
    "COMPLETE_DAY_BUFFER_MIN",
    "RESPONSIVE_PROFILE_MIN_OBSERVED_DAYS",
    "RESPONSIVE_PROFILE_MIN_VALID_LOGGING_DAYS7",
    "RESPONSIVE_PROFILE_MIN_CONSISTENCY28",
    "SELF_SUFFICIENT_PROFILE_MIN_CONSISTENCY28",
    "SELF_SUFFICIENT_PROFILE_MIN_VALID_DAYS14",
    "SELF_SUFFICIENT_PROFILE_MIN_AVG_MEALS14",
    "LOW_ENGAGEMENT_PROFILE_MAX_VALID_LOGGING_DAYS7",
    "LOW_ENGAGEMENT_PROFILE_MAX_CONSISTENCY28_EXCLUSIVE",
    "LOW_ENGAGEMENT_PROFILE_MAX_VALID_DAYS14",
    "SELF_SUFFICIENT_DIRECT_TIMING_ANCHOR_PROXIMITY_MIN",
    "LOW_ENGAGEMENT_DIRECT_TIMING_ANCHOR_PROXIMITY_MIN",
    "REASON_CODE_ORDER",
    "ReminderWindow",
    "ReminderQuietHours",
    "ReminderPreferencesInput",
    "ReminderActivityInput",
    "ReminderContextInput",
    "ReminderPersonalizationProfile",
    "ReminderTimingPolicy",
    "RESPONSIVE_TIMING_POLICY",
    "NEUTRAL_TIMING_POLICY",
    "SELF_SUFFICIENT_TIMING_POLICY",
    "LOW_ENGAGEMENT_TIMING_POLICY",
]
