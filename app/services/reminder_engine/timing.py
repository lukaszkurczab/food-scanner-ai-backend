from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Iterable

from app.schemas.reminders import ReminderReasonCode
from app.services.reminder_engine.suppression import _is_within_quiet_hours
from app.services.reminder_engine.types import (
    REASON_CODE_ORDER,
    ReminderQuietHours,
    ReminderTimingPolicy,
    ReminderWindow,
    UTC,
    _TimingCandidate,
    _WindowEvaluation,
)


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
    preferred_list = [c for c in candidates if c.source == "preferred"]
    habit_list = [c for c in candidates if c.source == "habit"]

    best_pref = preferred_list[0] if preferred_list else None

    if preferred_window is not None:
        habit_list = [
            h for h in habit_list if _is_minute_in_window(h.anchor_min, preferred_window)
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


def _select_best_habit(
    habits: list[_TimingCandidate], current_min: int
) -> _TimingCandidate | None:
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
        reason_codes=_ordered_reason_codes([day_reason, pref_reason, "habit_window_today"]),
        scheduled_at_local=deferred_at,
        valid_until_local=min(pref.valid_until_local, habit.valid_until_local),
    )


def _is_schedule_quiet_hours_safe(
    scheduled_at: datetime,
    quiet_hours: ReminderQuietHours | None,
) -> bool:
    if quiet_hours is None:
        return True
    return not _is_within_quiet_hours(_minute_of_day(scheduled_at), quiet_hours)


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
    return value.astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _end_of_local_day(now_local: datetime) -> datetime:
    return datetime.combine(now_local.date(), time(23, 59, 59), tzinfo=now_local.tzinfo)
