from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from math import ceil
from typing import Iterable, Literal

from app.schemas.nutrition_state import NutritionStateResponse
from app.schemas.reminders import ReminderDecision, ReminderReasonCode

logger = logging.getLogger(__name__)
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


def evaluate_reminder_decision(
    *,
    state: NutritionStateResponse,
    preferences: ReminderPreferencesInput,
    activity: ReminderActivityInput,
    context: ReminderContextInput,
) -> ReminderDecision:
    now_local = _normalize_local_datetime(context.now_local)
    current_min = _minute_of_day(now_local)
    computed_at = _to_utc_z(now_local)

    suppressions = _collect_suppression_reason_codes(
        preferences=preferences,
        activity=activity,
        current_min=current_min,
    )
    if suppressions:
        return ReminderDecision(
            dayKey=state.dayKey,
            computedAt=computed_at,
            decision="suppress",
            reasonCodes=suppressions,
            confidence=1.0,
            validUntil=_to_utc_z(
                _suppression_valid_until(
                    now_local=now_local,
                    reason_codes=suppressions,
                    quiet_hours=preferences.quiet_hours,
                )
            ),
        )

    if _is_day_already_complete(state):
        return ReminderDecision(
            dayKey=state.dayKey,
            computedAt=computed_at,
            decision="noop",
            reasonCodes=["day_already_complete"],
            confidence=0.98,
            validUntil=_to_utc_z(_end_of_local_day(now_local)),
        )

    profile = _build_personalization_profile(state=state)
    timing_policy = _timing_policy_for_profile(profile)
    logger.debug(
        "reminder.personalization.evaluated",
        extra={
            "day_key": state.dayKey,
            "segment": profile.segment,
            "timing_policy": _timing_policy_label(profile),
        },
    )

    if _is_day_empty(state):
        return _evaluate_first_meal_decision(
            state=state,
            preferences=preferences,
            now_local=now_local,
            current_min=current_min,
            computed_at=computed_at,
            timing_policy=timing_policy,
        )

    if _is_later_incomplete_day(state, current_min=current_min):
        complete_day_decision = _evaluate_complete_day_decision(
            state=state,
            preferences=preferences,
            now_local=now_local,
            current_min=current_min,
            computed_at=computed_at,
            timing_policy=timing_policy,
        )
        if complete_day_decision is not None:
            return complete_day_decision

    if _is_partially_logged_day(state):
        next_meal_decision = _evaluate_next_meal_decision(
            state=state,
            preferences=preferences,
            now_local=now_local,
            current_min=current_min,
            computed_at=computed_at,
            timing_policy=timing_policy,
        )
        if next_meal_decision is not None:
            return next_meal_decision

    return ReminderDecision(
        dayKey=state.dayKey,
        computedAt=computed_at,
        decision="noop",
        reasonCodes=["insufficient_signal"],
        confidence=0.65,
        validUntil=_to_utc_z(_end_of_local_day(now_local)),
    )


def _evaluate_first_meal_decision(
    *,
    state: NutritionStateResponse,
    preferences: ReminderPreferencesInput,
    now_local: datetime,
    current_min: int,
    computed_at: str,
    timing_policy: ReminderTimingPolicy,
) -> ReminderDecision:
    if not _has_enough_signal(state):
        return ReminderDecision(
            dayKey=state.dayKey,
            computedAt=computed_at,
            decision="noop",
            reasonCodes=["insufficient_signal"],
            confidence=0.74,
            validUntil=_to_utc_z(_end_of_local_day(now_local)),
        )

    window_evaluation = _evaluate_window_plan(
        now_local=now_local,
        current_min=current_min,
        preferred_window=preferences.first_meal_window,
        habit_minutes=_candidate_habit_minutes(state, "log_first_meal"),
        radius_min=FIRST_MEAL_WINDOW_RADIUS_MIN,
        day_reason="day_empty",
        observed_days=state.habits.behavior.timingPatterns14.observedDays,
        quiet_hours=preferences.quiet_hours,
        timing_policy=timing_policy,
    )
    if window_evaluation is None:
        return ReminderDecision(
            dayKey=state.dayKey,
            computedAt=computed_at,
            decision="noop",
            reasonCodes=["insufficient_signal"],
            confidence=0.7,
            validUntil=_to_utc_z(_end_of_local_day(now_local)),
        )

    return ReminderDecision(
        dayKey=state.dayKey,
        computedAt=computed_at,
        decision="send",
        kind="log_first_meal",
        reasonCodes=window_evaluation.reason_codes,
        scheduledAtUtc=_to_utc_z(window_evaluation.scheduled_at_local),
        confidence=0.87,
        validUntil=_to_utc_z(window_evaluation.valid_until_local),
    )


def _evaluate_next_meal_decision(
    *,
    state: NutritionStateResponse,
    preferences: ReminderPreferencesInput,
    now_local: datetime,
    current_min: int,
    computed_at: str,
    timing_policy: ReminderTimingPolicy,
) -> ReminderDecision | None:
    if not _has_enough_signal(state):
        return None

    window_evaluation = _evaluate_window_plan(
        now_local=now_local,
        current_min=current_min,
        preferred_window=preferences.next_meal_window,
        habit_minutes=_candidate_habit_minutes(state, "log_next_meal"),
        radius_min=NEXT_MEAL_WINDOW_RADIUS_MIN,
        day_reason="day_partially_logged",
        observed_days=state.habits.behavior.timingPatterns14.observedDays,
        quiet_hours=preferences.quiet_hours,
        timing_policy=timing_policy,
    )
    if window_evaluation is None:
        return None

    return ReminderDecision(
        dayKey=state.dayKey,
        computedAt=computed_at,
        decision="send",
        kind="log_next_meal",
        reasonCodes=window_evaluation.reason_codes,
        scheduledAtUtc=_to_utc_z(window_evaluation.scheduled_at_local),
        confidence=0.84,
        validUntil=_to_utc_z(window_evaluation.valid_until_local),
    )


def _evaluate_complete_day_decision(
    *,
    state: NutritionStateResponse,
    preferences: ReminderPreferencesInput,
    now_local: datetime,
    current_min: int,
    computed_at: str,
    timing_policy: ReminderTimingPolicy,
) -> ReminderDecision | None:
    if not _has_reasonable_complete_day_pattern(state):
        return None

    if state.quality.mealsLogged < _minimum_complete_day_meals(
        state,
        guarded=timing_policy.complete_day_guarded,
    ):
        return None

    window_evaluation = _evaluate_window_plan(
        now_local=now_local,
        current_min=current_min,
        preferred_window=preferences.complete_day_window,
        habit_minutes=_candidate_habit_minutes(state, "complete_day"),
        radius_min=COMPLETE_DAY_WINDOW_RADIUS_MIN,
        day_reason="day_partially_logged",
        observed_days=state.habits.behavior.timingPatterns14.observedDays,
        quiet_hours=preferences.quiet_hours,
        timing_policy=timing_policy,
    )
    if window_evaluation is None:
        return None

    return ReminderDecision(
        dayKey=state.dayKey,
        computedAt=computed_at,
        decision="send",
        kind="complete_day",
        reasonCodes=window_evaluation.reason_codes,
        scheduledAtUtc=_to_utc_z(window_evaluation.scheduled_at_local),
        confidence=0.8,
        validUntil=_to_utc_z(window_evaluation.valid_until_local),
    )


def _collect_suppression_reason_codes(
    *,
    preferences: ReminderPreferencesInput,
    activity: ReminderActivityInput,
    current_min: int,
) -> list[ReminderReasonCode]:
    reason_codes: list[ReminderReasonCode] = []

    if not preferences.reminders_enabled:
        reason_codes.append("reminders_disabled")
    if _is_within_quiet_hours(current_min, preferences.quiet_hours):
        reason_codes.append("quiet_hours")
    if activity.daily_send_count >= DAILY_REMINDER_CAP:
        reason_codes.append("frequency_cap_reached")
    if activity.already_logged_recently:
        reason_codes.append("already_logged_recently")
    if activity.recent_activity_detected:
        reason_codes.append("recent_activity_detected")

    return _ordered_reason_codes(reason_codes)


def _suppression_valid_until(
    *,
    now_local: datetime,
    reason_codes: list[ReminderReasonCode],
    quiet_hours: ReminderQuietHours | None,
) -> datetime:
    expiries: list[datetime] = []
    for reason_code in reason_codes:
        if reason_code == "quiet_hours":
            expiries.append(_quiet_hours_end(now_local, quiet_hours))
        elif reason_code in {"recent_activity_detected", "already_logged_recently"}:
            expiries.append(
                min(
                    now_local + timedelta(minutes=RECENT_ACTIVITY_SUPPRESSION_MIN),
                    _end_of_local_day(now_local),
                )
            )
        else:
            expiries.append(_end_of_local_day(now_local))
    return min(expiries)


def _evaluate_window_plan(
    *,
    now_local: datetime,
    current_min: int,
    preferred_window: ReminderWindow | None,
    habit_minutes: Iterable[int | None],
    radius_min: int,
    day_reason: ReminderReasonCode,
    observed_days: int,
    timing_policy: ReminderTimingPolicy,
    quiet_hours: ReminderQuietHours | None = None,
) -> _WindowEvaluation | None:
    candidates = _collect_timing_candidates(
        now_local=now_local,
        current_min=current_min,
        preferred_window=preferred_window,
        habit_minutes=habit_minutes,
        radius_min=radius_min,
        day_reason=day_reason,
        anchor_proximity_min=timing_policy.anchor_proximity_min,
    )
    if not candidates:
        return None

    # A configured preferred window is a hard bound. Once its candidate is
    # gone for today, habit timing must not resurrect scheduling outside it.
    if preferred_window is not None:
        has_preferred = any(c.source == "preferred" for c in candidates)
        if not has_preferred:
            return None

    evaluation = _resolve_timing_plan(
        candidates=candidates,
        current_min=current_min,
        now_local=now_local,
        preferred_window=preferred_window,
        observed_days=observed_days,
        day_reason=day_reason,
        timing_policy=timing_policy,
    )

    # Revalidate: deferred schedule must not land in quiet hours.
    # The suppression gate only checks current time; a deferred anchor
    # may fall into a future quiet-hours window.
    if not _is_schedule_quiet_hours_safe(evaluation.scheduled_at_local, quiet_hours):
        return None

    return evaluation


def _collect_timing_candidates(
    *,
    now_local: datetime,
    current_min: int,
    preferred_window: ReminderWindow | None,
    habit_minutes: Iterable[int | None],
    radius_min: int,
    day_reason: ReminderReasonCode,
    anchor_proximity_min: int,
) -> list[_TimingCandidate]:
    candidates: list[_TimingCandidate] = []

    preferred_candidate = _candidate_from_preferred_window(
        now_local=now_local,
        current_min=current_min,
        preferred_window=preferred_window,
        day_reason=day_reason,
    )
    if preferred_candidate is not None:
        candidates.append(preferred_candidate)

    for habit_min in habit_minutes:
        if habit_min is None:
            continue
        candidate = _candidate_from_habit_window(
            now_local=now_local,
            current_min=current_min,
            center_min=habit_min,
            radius_min=radius_min,
            day_reason=day_reason,
            anchor_proximity_min=anchor_proximity_min,
        )
        if candidate is not None:
            candidates.append(candidate)

    return candidates


def _resolve_timing_plan(
    *,
    candidates: list[_TimingCandidate],
    current_min: int,
    now_local: datetime,
    preferred_window: ReminderWindow | None,
    observed_days: int,
    day_reason: ReminderReasonCode,
    timing_policy: ReminderTimingPolicy,
) -> _WindowEvaluation:
    """Select the best timing from viable preferred/habit candidates."""
    preferred_list = [c for c in candidates if c.source == "preferred"]
    habit_list = [c for c in candidates if c.source == "habit"]

    best_pref = preferred_list[0] if preferred_list else None

    if preferred_window is not None:
        habit_list = [
            h for h in habit_list
            if _is_minute_in_window(h.anchor_min, preferred_window)
        ]

    best_habit = _select_best_habit(habit_list, current_min)

    if best_habit is None:
        assert best_pref is not None
        return _candidate_to_evaluation(best_pref)

    if best_pref is None:
        return _candidate_to_evaluation(best_habit)

    strong_signal = observed_days >= timing_policy.strong_habit_observed_days_min

    if best_pref.is_immediate:
        if best_habit.is_immediate:
            # The habit candidate already decided "send now" is acceptable,
            # so merge explainability from both preference and habit.
            return _merge_candidates_evaluation(best_pref, best_habit)

        if strong_signal and timing_policy.prefer_anchor_inside_window:
            return _deferred_anchor_evaluation(
                now_local=now_local,
                anchor_min=best_habit.anchor_min,
                pref=best_pref,
                habit=best_habit,
                day_reason=day_reason,
            )

        return _candidate_to_evaluation(best_pref)

    if strong_signal and timing_policy.prefer_anchor_inside_window:
        pref_start_min = _minute_of_day(best_pref.scheduled_at_local)
        if best_habit.anchor_min > pref_start_min:
            return _deferred_anchor_evaluation(
                now_local=now_local,
                anchor_min=best_habit.anchor_min,
                pref=best_pref,
                habit=best_habit,
                day_reason=day_reason,
            )

    return _candidate_to_evaluation(best_pref)


def _select_best_habit(
    habits: list[_TimingCandidate], current_min: int
) -> _TimingCandidate | None:
    """Pick the most relevant habit candidate.

    Prefers immediate candidates (currently in a habit window) with the
    anchor closest to *current_min*. Ties preserve the deterministic
    candidate order from ``_candidate_habit_minutes``. Falls back to the
    earliest future deferred candidate.
    """
    if not habits:
        return None
    immediate = [h for h in habits if h.is_immediate]
    if immediate:
        return min(immediate, key=lambda h: abs(h.anchor_min - current_min))
    return min(habits, key=lambda h: h.anchor_min)


def _candidate_to_evaluation(c: _TimingCandidate) -> _WindowEvaluation:
    return _WindowEvaluation(
        reason_codes=_ordered_reason_codes(c.reason_codes),
        scheduled_at_local=c.scheduled_at_local,
        valid_until_local=c.valid_until_local,
    )


def _merge_candidates_evaluation(
    pref: _TimingCandidate, habit: _TimingCandidate
) -> _WindowEvaluation:
    merged = list(pref.reason_codes) + list(habit.reason_codes)
    return _WindowEvaluation(
        reason_codes=_ordered_reason_codes(merged),
        scheduled_at_local=min(pref.scheduled_at_local, habit.scheduled_at_local),
        valid_until_local=min(pref.valid_until_local, habit.valid_until_local),
    )


def _deferred_anchor_evaluation(
    *,
    now_local: datetime,
    anchor_min: int,
    pref: _TimingCandidate,
    habit: _TimingCandidate,
    day_reason: ReminderReasonCode,
) -> _WindowEvaluation:
    anchor_hour, anchor_minute = divmod(anchor_min, 60)
    deferred_at = datetime.combine(
        now_local.date(),
        time(anchor_hour, anchor_minute, 0),
        tzinfo=now_local.tzinfo,
    )
    pref_reason: ReminderReasonCode = (
        "preferred_window_open" if pref.is_immediate else "preferred_window_today"
    )
    return _WindowEvaluation(
        reason_codes=_ordered_reason_codes(
            [day_reason, pref_reason, "habit_window_today"]
        ),
        scheduled_at_local=deferred_at,
        valid_until_local=min(pref.valid_until_local, habit.valid_until_local),
    )


def _candidate_from_preferred_window(
    *,
    now_local: datetime,
    current_min: int,
    preferred_window: ReminderWindow | None,
    day_reason: ReminderReasonCode,
) -> _TimingCandidate | None:
    if preferred_window is None:
        return None

    window_end = _window_end_datetime(now_local, preferred_window)
    if now_local > window_end:
        return None

    center_min = (preferred_window.start_min + preferred_window.end_min) // 2

    if _is_minute_in_window(current_min, preferred_window):
        return _TimingCandidate(
            source="preferred",
            scheduled_at_local=now_local,
            valid_until_local=window_end,
            anchor_min=center_min,
            is_immediate=True,
            reason_codes=[day_reason, "preferred_window_open"],
        )

    window_start = _window_start_datetime(now_local, preferred_window)
    if now_local < window_start:
        return _TimingCandidate(
            source="preferred",
            scheduled_at_local=window_start,
            valid_until_local=window_end,
            anchor_min=center_min,
            is_immediate=False,
            reason_codes=[day_reason, "preferred_window_today"],
        )

    return None


def _candidate_from_habit_window(
    *,
    now_local: datetime,
    current_min: int,
    center_min: int,
    radius_min: int,
    day_reason: ReminderReasonCode,
    anchor_proximity_min: int,
) -> _TimingCandidate | None:
    window_end = _center_window_end_datetime(
        now_local=now_local,
        center_min=center_min,
        radius_min=radius_min,
    )
    if now_local > window_end:
        return None

    if _is_within_center_window(current_min, center_min, radius_min):
        if _is_far_before_anchor(
            current_min=current_min,
            anchor_min=center_min,
            proximity_min=anchor_proximity_min,
        ):
            # The broad habit window marks the day as relevant, but send-now is
            # only justified when the upcoming anchor is actually near.
            anchor_hour, anchor_minute = divmod(center_min, 60)
            anchor_dt = datetime.combine(
                now_local.date(),
                time(anchor_hour, anchor_minute, 0),
                tzinfo=now_local.tzinfo,
            )
            return _TimingCandidate(
                source="habit",
                scheduled_at_local=anchor_dt,
                valid_until_local=window_end,
                anchor_min=center_min,
                is_immediate=False,
                reason_codes=[day_reason, "habit_window_today"],
            )
        return _TimingCandidate(
            source="habit",
            scheduled_at_local=now_local,
            valid_until_local=window_end,
            anchor_min=center_min,
            is_immediate=True,
            reason_codes=[day_reason, "habit_window_match", "logging_usually_happens_now"],
        )

    window_start = _center_window_start_datetime(
        now_local=now_local,
        center_min=center_min,
        radius_min=radius_min,
    )
    if now_local < window_start:
        return _TimingCandidate(
            source="habit",
            scheduled_at_local=window_start,
            valid_until_local=window_end,
            anchor_min=center_min,
            is_immediate=False,
            reason_codes=[day_reason, "habit_window_today"],
        )

    return None


def _build_personalization_profile(
    *,
    state: NutritionStateResponse,
) -> ReminderPersonalizationProfile:
    """Classify reminder style from existing behavior signals only.

    The profile is intentionally conservative: it relies on stable habit/logging
    inputs already present in ``NutritionStateResponse`` and ignores delivery
    outcomes, which the decision layer does not own.
    """

    behavior = state.habits.behavior
    timing = behavior.timingPatterns14

    if (
        behavior.validLoggingConsistency28 >= SELF_SUFFICIENT_PROFILE_MIN_CONSISTENCY28
        and behavior.dayCoverage14.validLoggedDays >= SELF_SUFFICIENT_PROFILE_MIN_VALID_DAYS14
        and behavior.avgValidMealsPerValidLoggedDay14 >= SELF_SUFFICIENT_PROFILE_MIN_AVG_MEALS14
    ):
        return ReminderPersonalizationProfile(segment="self_sufficient")

    if (
        timing.observedDays >= RESPONSIVE_PROFILE_MIN_OBSERVED_DAYS
        and behavior.validLoggingDays7 >= RESPONSIVE_PROFILE_MIN_VALID_LOGGING_DAYS7
        and behavior.validLoggingConsistency28 >= RESPONSIVE_PROFILE_MIN_CONSISTENCY28
    ):
        return ReminderPersonalizationProfile(segment="responsive")

    if (
        behavior.validLoggingDays7 <= LOW_ENGAGEMENT_PROFILE_MAX_VALID_LOGGING_DAYS7
        or behavior.validLoggingConsistency28 < LOW_ENGAGEMENT_PROFILE_MAX_CONSISTENCY28_EXCLUSIVE
        or behavior.dayCoverage14.validLoggedDays <= LOW_ENGAGEMENT_PROFILE_MAX_VALID_DAYS14
    ):
        return ReminderPersonalizationProfile(segment="low_engagement")

    return ReminderPersonalizationProfile(segment="neutral")


def _timing_policy_for_profile(
    profile: ReminderPersonalizationProfile,
) -> ReminderTimingPolicy:
    """Return bounded timing knobs for a profile.

    These knobs may bias timing selection, but they must not bypass
    suppressions, quiet hours, or preferred-window hard bounds.
    """
    if profile.segment == "responsive":
        return RESPONSIVE_TIMING_POLICY
    if profile.segment == "low_engagement":
        return LOW_ENGAGEMENT_TIMING_POLICY
    if profile.segment == "self_sufficient":
        return SELF_SUFFICIENT_TIMING_POLICY
    return NEUTRAL_TIMING_POLICY


def _timing_policy_label(profile: ReminderPersonalizationProfile) -> str:
    if profile.segment == "responsive":
        return "responsive"
    if profile.segment == "low_engagement":
        return "low_engagement_direct"
    if profile.segment == "self_sufficient":
        return "self_sufficient_direct"
    return "neutral"


def _has_enough_signal(state: NutritionStateResponse) -> bool:
    if not state.habits.available:
        return False

    timing = state.habits.behavior.timingPatterns14
    behavior = state.habits.behavior

    return (
        timing.available
        and timing.observedDays >= 3
        and behavior.validLoggingDays7 >= 2
        and behavior.dayCoverage14.validLoggedDays >= 3
        and state.quality.dataCompletenessScore >= 0.35
    )


def _has_reasonable_complete_day_pattern(state: NutritionStateResponse) -> bool:
    if not state.habits.available:
        return False

    timing = state.habits.behavior.timingPatterns14
    behavior = state.habits.behavior

    return (
        behavior.validLoggingConsistency28 >= 0.35
        and behavior.dayCoverage14.validLoggedDays >= 2
        and timing.available
        and timing.observedDays >= 4
        and timing.lastMealMedianHour is not None
    )


def _is_day_empty(state: NutritionStateResponse) -> bool:
    return state.quality.mealsLogged <= 0


def _is_day_already_complete(state: NutritionStateResponse) -> bool:
    expected_meals = _expected_complete_meals(state)
    return (
        state.quality.mealsLogged >= expected_meals
        and state.quality.missingNutritionMeals == 0
        and state.quality.dataCompletenessScore >= 0.95
    )


def _is_partially_logged_day(state: NutritionStateResponse) -> bool:
    return state.quality.mealsLogged > 0 and not _is_day_already_complete(state)


def _is_later_incomplete_day(state: NutritionStateResponse, *, current_min: int) -> bool:
    if not _is_partially_logged_day(state):
        return False

    later_start_min = max(
        LATEST_COMPLETE_DAY_START_MIN,
        _complete_day_anchor_min(state),
    )
    return current_min >= later_start_min


def _expected_complete_meals(state: NutritionStateResponse) -> int:
    habitual_average = state.habits.behavior.avgValidMealsPerValidLoggedDay14
    if habitual_average <= 0:
        return 3
    return max(3, ceil(habitual_average))


def _complete_day_anchor_min(state: NutritionStateResponse) -> int:
    timing = state.habits.behavior.timingPatterns14
    if timing.lastMealMedianHour is None:
        return LATEST_COMPLETE_DAY_START_MIN
    return max(
        LATEST_COMPLETE_DAY_START_MIN,
        int(timing.lastMealMedianHour * 60) + COMPLETE_DAY_BUFFER_MIN,
    )


def _minimum_complete_day_meals(
    state: NutritionStateResponse,
    *,
    guarded: bool = False,
) -> int:
    expected_meals = _expected_complete_meals(state)
    if guarded:
        return expected_meals
    return max(1, expected_meals - 1)


def _candidate_habit_minutes(
    state: NutritionStateResponse,
    kind: str,
) -> tuple[int | None, ...]:
    timing = state.habits.behavior.timingPatterns14
    if kind == "log_first_meal":
        return (_hour_to_minute(timing.firstMealMedianHour),)
    if kind == "log_next_meal":
        return (
            _hour_to_minute(timing.breakfastMedianHour),
            _hour_to_minute(timing.lunchMedianHour),
            _hour_to_minute(timing.dinnerMedianHour),
            _hour_to_minute(timing.snackMedianHour),
            _hour_to_minute(timing.lastMealMedianHour),
        )
    last_meal_min = _hour_to_minute(timing.lastMealMedianHour)
    if last_meal_min is None:
        return (None,)
    return (last_meal_min + COMPLETE_DAY_BUFFER_MIN,)


def _hour_to_minute(value: float | None) -> int | None:
    if value is None:
        return None
    return int(value * 60)


def _ordered_reason_codes(reason_codes: Iterable[ReminderReasonCode]) -> list[ReminderReasonCode]:
    seen = set(reason_codes)
    return [code for code in REASON_CODE_ORDER if code in seen]


def _normalize_local_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value


def _minute_of_day(value: datetime) -> int:
    return value.hour * 60 + value.minute


def _to_utc_z(value: datetime) -> str:
    """Serialize *value* to canonical ``YYYY-MM-DDTHH:MM:SSZ`` (exactly 20 chars).

    The explicit ``strftime`` avoids ``isoformat()`` emitting sub-second
    precision when the source datetime carries microseconds – which would
    violate the contract enforced by :class:`ReminderDecision`.
    """
    return value.astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _end_of_local_day(now_local: datetime) -> datetime:
    return datetime.combine(now_local.date(), time(23, 59, 59), tzinfo=now_local.tzinfo)


def _is_schedule_quiet_hours_safe(
    scheduled_at: datetime,
    quiet_hours: ReminderQuietHours | None,
) -> bool:
    """Return True if *scheduled_at* does not fall inside quiet hours."""
    if quiet_hours is None:
        return True
    return not _is_within_quiet_hours(_minute_of_day(scheduled_at), quiet_hours)


def _is_within_quiet_hours(current_min: int, quiet_hours: ReminderQuietHours | None) -> bool:
    if quiet_hours is None:
        return False

    start_min = quiet_hours.start_hour * 60
    end_min = quiet_hours.end_hour * 60

    if start_min == end_min:
        return True
    if start_min < end_min:
        return start_min <= current_min < end_min
    return current_min >= start_min or current_min < end_min


def _quiet_hours_end(
    now_local: datetime,
    quiet_hours: ReminderQuietHours | None,
) -> datetime:
    if quiet_hours is None:
        return _end_of_local_day(now_local)

    end_time = time(quiet_hours.end_hour, 0, 0)
    current_min = _minute_of_day(now_local)
    end_min = quiet_hours.end_hour * 60
    same_day_end = datetime.combine(now_local.date(), end_time, tzinfo=now_local.tzinfo)

    if _is_within_quiet_hours(current_min, quiet_hours) and current_min < end_min:
        return same_day_end
    return same_day_end + timedelta(days=1)


def _is_minute_in_window(current_min: int, window: ReminderWindow) -> bool:
    if window.start_min == window.end_min:
        return True
    if window.start_min < window.end_min:
        return window.start_min <= current_min <= window.end_min
    return current_min >= window.start_min or current_min <= window.end_min


def _is_far_before_anchor(
    *,
    current_min: int,
    anchor_min: int,
    proximity_min: int,
) -> bool:
    return anchor_min > current_min and (anchor_min - current_min) > proximity_min


def _window_end_datetime(now_local: datetime, window: ReminderWindow) -> datetime:
    end_hour, end_minute = divmod(window.end_min, 60)
    end_time = time(end_hour % 24, end_minute, 0)
    same_day_end = datetime.combine(now_local.date(), end_time, tzinfo=now_local.tzinfo)

    if window.start_min == window.end_min:
        return _end_of_local_day(now_local)
    if window.start_min < window.end_min:
        return same_day_end
    if _minute_of_day(now_local) <= window.end_min:
        return same_day_end
    return same_day_end + timedelta(days=1)


def _window_start_datetime(now_local: datetime, window: ReminderWindow) -> datetime:
    start_hour, start_minute = divmod(window.start_min, 60)
    return datetime.combine(
        now_local.date(),
        time(start_hour % 24, start_minute, 0),
        tzinfo=now_local.tzinfo,
    )


def _is_within_center_window(current_min: int, center_min: int, radius_min: int) -> bool:
    start_min = max(0, center_min - radius_min)
    end_min = min(1439, center_min + radius_min)
    return start_min <= current_min <= end_min


def _center_window_end_datetime(
    *,
    now_local: datetime,
    center_min: int,
    radius_min: int,
) -> datetime:
    end_min = min(1439, center_min + radius_min)
    end_hour, end_minute = divmod(end_min, 60)
    return datetime.combine(
        now_local.date(),
        time(end_hour, end_minute, 0),
        tzinfo=now_local.tzinfo,
    )


def _center_window_start_datetime(
    *,
    now_local: datetime,
    center_min: int,
    radius_min: int,
) -> datetime:
    start_min = max(0, center_min - radius_min)
    start_hour, start_minute = divmod(start_min, 60)
    return datetime.combine(
        now_local.date(),
        time(start_hour, start_minute, 0),
        tzinfo=now_local.tzinfo,
    )
