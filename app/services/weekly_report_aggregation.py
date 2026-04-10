from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import logging
from typing import Any

from firebase_admin.exceptions import FirebaseError
from google.api_core.exceptions import GoogleAPICallError, RetryError
from google.cloud import firestore

from app.core.exceptions import FirestoreServiceError
from app.core.firestore_constants import USERS_COLLECTION
from app.db.firebase import get_firestore
from app.schemas.weekly_reports import WeeklyReportPeriod
from app.services.habit_signal_service import DailyAggregate, _aggregate_days
from app.services.nutrition_state_service import _filter_core_meals, _load_bounded_meals

logger = logging.getLogger(__name__)
UTC = timezone.utc

WEEKLY_REPORT_WINDOW_DAYS = 7
WEEKLY_REPORT_READ_WINDOW_DAYS = 14


@dataclass(frozen=True)
class WeeklyDayAggregate:
    day_key: str
    is_weekend: bool
    meal_count: int
    valid_meal_count: int
    has_unknown_meal_details: bool
    first_logged_at_local_min: int | None
    last_logged_at_local_min: int | None
    valid_meal_types: tuple[str, ...]
    kcal: float
    protein: float


@dataclass(frozen=True)
class WeeklyAggregate:
    period: WeeklyReportPeriod
    previous_period: WeeklyReportPeriod | None
    days: tuple[WeeklyDayAggregate, ...]
    previous_days: tuple[WeeklyDayAggregate, ...]


def _to_date(day_key: str) -> date:
    return datetime.strptime(day_key, "%Y-%m-%d").date()


def _period_end_midday(period: WeeklyReportPeriod) -> datetime:
    return datetime.combine(_to_date(period.endDay), time(hour=12), tzinfo=UTC)


def _build_period(start_day: date, end_day: date) -> WeeklyReportPeriod:
    return WeeklyReportPeriod(
        startDay=start_day.isoformat(),
        endDay=end_day.isoformat(),
    )


def _build_previous_period(period: WeeklyReportPeriod) -> WeeklyReportPeriod:
    start_day = _to_date(period.startDay)
    previous_end = start_day - timedelta(days=1)
    previous_start = previous_end - timedelta(days=WEEKLY_REPORT_WINDOW_DAYS - 1)
    return _build_period(previous_start, previous_end)


def _build_day_aggregate(day_key: str, aggregate: DailyAggregate | None) -> WeeklyDayAggregate:
    normalized = aggregate or DailyAggregate()
    timing_minutes = sorted(normalized.timing_minutes)
    return WeeklyDayAggregate(
        day_key=day_key,
        is_weekend=_to_date(day_key).weekday() >= 5,
        meal_count=normalized.meal_count,
        valid_meal_count=normalized.valid_meal_count,
        has_unknown_meal_details=normalized.has_unknown_details,
        first_logged_at_local_min=timing_minutes[0] if timing_minutes else None,
        last_logged_at_local_min=timing_minutes[-1] if timing_minutes else None,
        valid_meal_types=tuple(sorted(normalized.valid_meal_types)),
        kcal=normalized.kcal,
        protein=normalized.protein,
    )


def _build_days_for_period(
    period: WeeklyReportPeriod,
    aggregates: dict[str, DailyAggregate],
) -> tuple[WeeklyDayAggregate, ...]:
    start_day = _to_date(period.startDay)
    return tuple(
        _build_day_aggregate(
            (start_day + timedelta(days=index)).isoformat(),
            aggregates.get((start_day + timedelta(days=index)).isoformat()),
        )
        for index in range(WEEKLY_REPORT_WINDOW_DAYS)
    )


def build_weekly_aggregate_from_meals(
    *,
    period: WeeklyReportPeriod,
    meals: list[dict[str, Any]],
) -> WeeklyAggregate:
    computed_at = _period_end_midday(period)
    aggregates = _aggregate_days(meals, computed_at=computed_at)
    previous_period = _build_previous_period(period)

    return WeeklyAggregate(
        period=period,
        previous_period=previous_period,
        days=_build_days_for_period(period, aggregates),
        previous_days=_build_days_for_period(previous_period, aggregates),
    )


def collect_weekly_aggregate(
    user_id: str,
    *,
    period: WeeklyReportPeriod,
) -> WeeklyAggregate:
    client: firestore.Client = get_firestore()
    user_ref = client.collection(USERS_COLLECTION).document(user_id)

    try:
        bounded_meals = _load_bounded_meals(
            user_ref,
            reference_day_key=period.endDay,
            window_days=WEEKLY_REPORT_READ_WINDOW_DAYS,
        )
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to collect weekly report aggregate.",
            extra={"user_id": user_id, "period_end": period.endDay},
        )
        raise FirestoreServiceError("Failed to collect weekly report aggregate.") from exc

    meals = _filter_core_meals(bounded_meals)
    return build_weekly_aggregate_from_meals(period=period, meals=meals)
