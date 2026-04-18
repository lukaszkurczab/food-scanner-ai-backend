"""Behavioral signal derivation from user meal history."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
import logging
from typing import Any, cast

from firebase_admin.exceptions import FirebaseError
from google.api_core.exceptions import GoogleAPICallError, RetryError
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from app.core.coercion import coerce_float, round_metric
from app.core.datetime_utils import parse_flexible_datetime, utc_now
from app.core.exceptions import FirestoreServiceError
from app.core.firestore_constants import MEALS_SUBCOLLECTION, USERS_COLLECTION
from app.core.firestore_query_fallback import stream_with_missing_index_fallback
from app.db.firebase import get_firestore
from app.schemas.habits import (
    CoachPriority,
    DayCoverage14,
    HabitBehavior,
    HabitDataQuality,
    HabitTimingPatterns14,
    HabitSignalsResponse,
    MealTypeCoverage14,
    MealTypeFrequency14,
    ProteinDaysHit14,
    TopRisk,
)
from app.services.nutrition_target_service import parse_target_kcal

logger = logging.getLogger(__name__)
UTC = timezone.utc

VALID_MEAL_TYPES = ("breakfast", "lunch", "dinner", "snack", "other")
LOW_CONFIDENCE_THRESHOLD = 0.5
RECENT_ACTIVITY_WINDOW_DAYS = 7
ADHERENCE_WINDOW_DAYS = 14
CONSISTENCY_WINDOW_DAYS = 28
READ_WINDOW_BUFFER_DAYS = 1


@dataclass(frozen=True)
class NormalizedMeal:
    day_key: str
    meal_type: str
    kcal: float
    protein: float
    unknown_details: bool
    timing_minute: int | None
    used_timestamp_day_fallback: bool = False
    used_timestamp_timing_fallback: bool = False


def _new_str_set() -> set[str]:
    return set()


def _new_int_list() -> list[int]:
    return []


def _new_timing_dict() -> dict[str, list[int]]:
    return {}


@dataclass
class DailyAggregate:
    meal_count: int = 0
    valid_meal_count: int = 0
    kcal: float = 0.0
    protein: float = 0.0
    meal_types: set[str] = field(default_factory=_new_str_set)
    valid_meal_types: set[str] = field(default_factory=_new_str_set)
    has_unknown_details: bool = False
    timing_minutes: list[int] = field(default_factory=_new_int_list)
    timing_minutes_by_type: dict[str, list[int]] = field(default_factory=_new_timing_dict)
    used_timestamp_day_fallback: bool = False
    used_timestamp_timing_fallback: bool = False


def _to_date(day_key: str) -> date | None:
    try:
        return datetime.strptime(day_key, "%Y-%m-%d").date()
    except ValueError:
        return None


def _derive_day_key_parts(raw_meal: dict[str, Any]) -> tuple[str | None, bool]:
    """Use valid dayKey as the habit source of truth, otherwise fall back to UTC timestamp day."""
    day_key = raw_meal.get("dayKey")
    if isinstance(day_key, str):
        normalized_day_key = day_key.strip()
        if _to_date(normalized_day_key) is not None:
            return normalized_day_key, False

    timestamp = parse_flexible_datetime(raw_meal.get("timestamp"))
    if timestamp is None:
        return None, False
    return timestamp.date().isoformat(), True


def _derive_day_key(raw_meal: dict[str, Any]) -> str | None:
    day_key, _ = _derive_day_key_parts(raw_meal)
    return day_key


def _as_object_map(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    raw_map = cast(dict[object, object], value)
    result: dict[str, object] = {}
    for raw_key, raw_item in raw_map.items():
        if isinstance(raw_key, str):
            result[raw_key] = raw_item
    return result


def _parse_wall_clock_minute(value: Any) -> int | None:
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return None
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            parsed = parse_flexible_datetime(value)
        if parsed is None:
            return None
        return parsed.hour * 60 + parsed.minute

    parsed = parse_flexible_datetime(value)
    if parsed is None:
        return None
    return parsed.hour * 60 + parsed.minute


def _extract_logged_at_local_min(raw_meal: dict[str, Any]) -> tuple[int | None, bool]:
    explicit = raw_meal.get("loggedAtLocalMin")
    if isinstance(explicit, int) and 0 <= explicit <= 1439:
        return explicit, False
    if isinstance(explicit, float) and 0 <= int(explicit) <= 1439:
        return int(explicit), False

    fallback_minute = _parse_wall_clock_minute(raw_meal.get("timestamp"))
    if fallback_minute is None:
        return None, False
    return fallback_minute, True




def _extract_totals(raw_meal: dict[str, Any]) -> tuple[float, float]:
    totals_map = _as_object_map(raw_meal.get("totals"))
    if totals_map is not None:
        kcal = coerce_float(totals_map.get("kcal"))
        protein = coerce_float(totals_map.get("protein"))
        if kcal > 0 or protein > 0:
            return kcal, protein

    kcal = 0.0
    protein = 0.0
    ingredients = raw_meal.get("ingredients")
    if isinstance(ingredients, list):
        ingredients_list = cast(list[object], ingredients)
        for raw_ingredient in ingredients_list:
            ingredient_map = _as_object_map(raw_ingredient)
            if ingredient_map is None:
                continue
            kcal += coerce_float(ingredient_map.get("kcal"))
            protein += coerce_float(ingredient_map.get("protein"))
    return kcal, protein


def _has_meaningful_ingredients(raw_meal: dict[str, Any]) -> bool:
    ingredients = raw_meal.get("ingredients")
    if not isinstance(ingredients, list):
        return False

    ingredients_list = cast(list[object], ingredients)
    for raw_ingredient in ingredients_list:
        ingredient_map = _as_object_map(raw_ingredient)
        if ingredient_map is None:
            continue
        ingredient_name = ingredient_map.get("name")
        if isinstance(ingredient_name, str) and ingredient_name.strip():
            return True
        if any(
            coerce_float(ingredient_map.get(field_name)) > 0
            for field_name in ("amount", "kcal", "protein", "carbs", "fat")
        ):
            return True
    return False


def _has_meaningful_totals(raw_meal: dict[str, Any]) -> bool:
    kcal, protein = _extract_totals(raw_meal)
    if kcal > 0 or protein > 0:
        return True

    totals_map = _as_object_map(raw_meal.get("totals"))
    if totals_map is None:
        return False
    return any(
        coerce_float(totals_map.get(field_name)) > 0
        for field_name in ("carbs", "fat")
    )


def _parse_ai_meta(raw_meal: dict[str, Any]) -> dict[str, object] | None:
    for key in ("aiMeta", "ai_meta"):
        value = raw_meal.get(key)
        value_map = _as_object_map(value)
        if value_map is not None:
            return value_map
    return None


def _has_low_confidence_ai_meta(raw_meal: dict[str, Any]) -> bool:
    ai_meta = _parse_ai_meta(raw_meal)
    if ai_meta is None:
        return False
    confidence = ai_meta.get("confidence")
    return coerce_float(confidence, default=1.0) < LOW_CONFIDENCE_THRESHOLD


def _is_structurally_incomplete(raw_meal: dict[str, Any]) -> bool:
    if not str(raw_meal.get("mealId") or raw_meal.get("cloudId") or "").strip():
        return True
    if _derive_day_key(raw_meal) is None:
        return True
    meal_type = raw_meal.get("type")
    return not isinstance(meal_type, str) or meal_type not in VALID_MEAL_TYPES


def _is_unknown_meal_details(raw_meal: dict[str, Any]) -> bool:
    return (
        (not _has_meaningful_ingredients(raw_meal) and not _has_meaningful_totals(raw_meal))
        or _has_low_confidence_ai_meta(raw_meal)
        or _is_structurally_incomplete(raw_meal)
    )


def _is_deleted_meal(raw_meal: dict[str, Any]) -> bool:
    return bool(raw_meal.get("deleted"))


def _normalize_meal(raw_meal: dict[str, Any]) -> NormalizedMeal | None:
    day_key, used_timestamp_day_fallback = _derive_day_key_parts(raw_meal)
    if day_key is None:
        return None

    meal_type = raw_meal.get("type")
    normalized_meal_type = meal_type if isinstance(meal_type, str) and meal_type in VALID_MEAL_TYPES else "other"
    kcal, protein = _extract_totals(raw_meal)
    timing_minute, used_timestamp_timing_fallback = _extract_logged_at_local_min(raw_meal)

    return NormalizedMeal(
        day_key=day_key,
        meal_type=normalized_meal_type,
        kcal=kcal,
        protein=protein,
        unknown_details=_is_unknown_meal_details(raw_meal),
        timing_minute=timing_minute,
        used_timestamp_day_fallback=used_timestamp_day_fallback,
        used_timestamp_timing_fallback=used_timestamp_timing_fallback,
    )


def _extract_protein_target(raw_user: dict[str, Any] | None) -> float | None:
    if not isinstance(raw_user, dict):
        return None

    for key in ("proteinTarget", "targetProtein", "proteinGoal"):
        value = raw_user.get(key)
        if isinstance(value, (int, float)) and float(value) > 0:
            return float(value)

    macro_targets = _as_object_map(raw_user.get("macroTargets"))
    if macro_targets is not None:
        for key in ("proteinGrams", "protein"):
            value = macro_targets.get(key)
            if isinstance(value, (int, float)) and float(value) > 0:
                return float(value)

    return None


def _in_window(day_value: date, *, end_day: date, window_days: int) -> bool:
    start_day = end_day - timedelta(days=window_days - 1)
    return start_day <= day_value <= end_day


def _aggregate_days(
    meals: list[dict[str, Any]],
    *,
    computed_at: datetime,
) -> dict[str, DailyAggregate]:
    end_day = computed_at.date()
    aggregates: dict[str, DailyAggregate] = {}

    for raw_meal in meals:
        if _is_deleted_meal(raw_meal):
            continue
        normalized = _normalize_meal(raw_meal)
        if normalized is None:
            continue
        day_value = _to_date(normalized.day_key)
        if day_value is None or not _in_window(
            day_value,
            end_day=end_day,
            window_days=CONSISTENCY_WINDOW_DAYS,
        ):
            continue

        aggregate = aggregates.setdefault(normalized.day_key, DailyAggregate())
        aggregate.meal_count += 1
        aggregate.meal_types.add(normalized.meal_type)
        aggregate.has_unknown_details = (
            aggregate.has_unknown_details or normalized.unknown_details
        )
        aggregate.used_timestamp_day_fallback = (
            aggregate.used_timestamp_day_fallback or normalized.used_timestamp_day_fallback
        )
        aggregate.used_timestamp_timing_fallback = (
            aggregate.used_timestamp_timing_fallback or normalized.used_timestamp_timing_fallback
        )
        if normalized.timing_minute is not None:
            aggregate.timing_minutes.append(normalized.timing_minute)
            aggregate.timing_minutes_by_type.setdefault(normalized.meal_type, []).append(
                normalized.timing_minute
            )

        if normalized.unknown_details:
            continue

        aggregate.valid_meal_count += 1
        aggregate.kcal += normalized.kcal
        aggregate.protein += normalized.protein
        aggregate.valid_meal_types.add(normalized.meal_type)

    return aggregates


def _sorted_day_keys_for_window(
    aggregates: dict[str, DailyAggregate],
    *,
    computed_at: datetime,
    window_days: int,
) -> list[str]:
    end_day = computed_at.date()
    filtered = [
        day_key
        for day_key in aggregates
        if (day_value := _to_date(day_key)) is not None
        and _in_window(day_value, end_day=end_day, window_days=window_days)
    ]
    return sorted(filtered)


def _build_meal_type_coverage(day_keys: list[str], aggregates: dict[str, DailyAggregate]) -> MealTypeCoverage14:
    covered_types = {
        meal_type
        for day_key in day_keys
        for meal_type in aggregates[day_key].valid_meal_types
    }
    return MealTypeCoverage14(
        breakfast="breakfast" in covered_types,
        lunch="lunch" in covered_types,
        dinner="dinner" in covered_types,
        snack="snack" in covered_types,
        other="other" in covered_types,
        coveredCount=len(covered_types),
    )


def _build_meal_type_frequency(
    day_keys: list[str],
    aggregates: dict[str, DailyAggregate],
) -> MealTypeFrequency14:
    frequency_by_type = {
        meal_type: sum(
            1
            for day_key in day_keys
            if meal_type in aggregates[day_key].valid_meal_types
        )
        for meal_type in VALID_MEAL_TYPES
    }
    return MealTypeFrequency14(**frequency_by_type)


def _build_protein_days_hit(
    *,
    day_keys_14: list[str],
    aggregates: dict[str, DailyAggregate],
    protein_target: float | None,
) -> ProteinDaysHit14:
    if protein_target is None or protein_target <= 0:
        return ProteinDaysHit14(
            hitDays=0,
            eligibleDays=0,
            unknownDays=len(day_keys_14),
            ratio=None,
        )

    hit_days = sum(
        1
        for day_key in day_keys_14
        if aggregates[day_key].protein >= protein_target * 0.9
    )
    eligible_days = len(day_keys_14)
    return ProteinDaysHit14(
        hitDays=hit_days,
        eligibleDays=eligible_days,
        unknownDays=0,
        ratio=round_metric(hit_days / eligible_days) if eligible_days else None,
    )


def _median(values: list[int]) -> float | None:
    if not values:
        return None
    sorted_values = sorted(values)
    mid = len(sorted_values) // 2
    if len(sorted_values) % 2 == 1:
        return float(sorted_values[mid])
    return (sorted_values[mid - 1] + sorted_values[mid]) / 2


def _median_hour(values: list[int]) -> float | None:
    median_minute = _median(values)
    if median_minute is None:
        return None
    return round_metric(median_minute / 60, 2)


def _build_timing_patterns(
    day_keys: list[str],
    aggregates: dict[str, DailyAggregate],
) -> HabitTimingPatterns14:
    timing_day_values = [
        sorted(aggregates[day_key].timing_minutes)
        for day_key in day_keys
        if aggregates[day_key].timing_minutes
    ]
    observed_days = len(timing_day_values)
    if observed_days == 0:
        return HabitTimingPatterns14()

    first_meal_minutes = [day_values[0] for day_values in timing_day_values]
    last_meal_minutes = [day_values[-1] for day_values in timing_day_values]
    eating_window_minutes = [day_values[-1] - day_values[0] for day_values in timing_day_values]

    def _minutes_for(meal_type: str) -> list[int]:
        return [
            minute
            for day_key in day_keys
            for minute in aggregates[day_key].timing_minutes_by_type.get(meal_type, [])
        ]

    return HabitTimingPatterns14(
        available=True,
        observedDays=observed_days,
        firstMealMedianHour=_median_hour(first_meal_minutes),
        lastMealMedianHour=_median_hour(last_meal_minutes),
        eatingWindowHoursMedian=round_metric((_median(eating_window_minutes) or 0) / 60, 2),
        breakfastMedianHour=_median_hour(_minutes_for("breakfast")),
        lunchMedianHour=_median_hour(_minutes_for("lunch")),
        dinnerMedianHour=_median_hour(_minutes_for("dinner")),
        snackMedianHour=_median_hour(_minutes_for("snack")),
        otherMedianHour=_median_hour(_minutes_for("other")),
    )


def _derive_top_risk_and_priority(
    *,
    valid_logging_days_7: int,
    valid_logging_consistency_28: float,
    days_with_unknown_details_14: int,
    logged_days_14: int,
    kcal_under_target_ratio_14: float | None,
    protein_days_hit_14: ProteinDaysHit14,
) -> tuple[TopRisk, CoachPriority]:
    if valid_logging_days_7 < 4 or valid_logging_consistency_28 < 0.5:
        return "under_logging", "logging_foundation"

    if logged_days_14 > 0 and days_with_unknown_details_14 / logged_days_14 >= 0.4:
        return "high_unknown_meal_details", "meal_detail_quality"

    if protein_days_hit_14.ratio is not None and protein_days_hit_14.ratio < 0.5:
        return "low_protein_consistency", "protein_consistency"

    if kcal_under_target_ratio_14 is not None and kcal_under_target_ratio_14 >= 0.6:
        return "calorie_under_target", "calorie_adherence"

    return "none", "maintain"


def _build_read_window(
    *,
    computed_at: datetime,
    window_days: int = CONSISTENCY_WINDOW_DAYS,
    buffer_days: int = READ_WINDOW_BUFFER_DAYS,
) -> tuple[date, date]:
    end_day = computed_at.date()
    start_day = end_day - timedelta(days=window_days - 1)
    return start_day - timedelta(days=buffer_days), end_day + timedelta(days=buffer_days)


def _serialize_day_start(day_value: date) -> str:
    return datetime.combine(day_value, time.min, tzinfo=UTC).isoformat().replace("+00:00", "Z")


def _load_recent_meals(
    user_ref: firestore.DocumentReference,
    *,
    computed_at: datetime,
) -> list[dict[str, Any]]:
    meals_collection = user_ref.collection(MEALS_SUBCOLLECTION)
    start_day, end_day = _build_read_window(computed_at=computed_at)
    start_day_key = start_day.isoformat()
    end_day_key = end_day.isoformat()
    start_ts = _serialize_day_start(start_day)
    end_ts = _serialize_day_start(end_day + timedelta(days=1))

    # Read by canonical dayKey first, then add a bounded timestamp fallback for
    # meals missing/invalid dayKey. If the composite index for the deleted
    # filter is unavailable, retry with the same bounded range and filter
    # deleted meals during aggregation.
    snapshots_by_id: dict[str, dict[str, Any]] = {}
    day_key_query = (
        meals_collection.where(filter=FieldFilter("deleted", "==", False))
        .where(filter=FieldFilter("dayKey", ">=", start_day_key))
        .where(filter=FieldFilter("dayKey", "<=", end_day_key))
    )
    day_key_fallback_query = (
        meals_collection.where(filter=FieldFilter("dayKey", ">=", start_day_key))
        .where(filter=FieldFilter("dayKey", "<=", end_day_key))
    )
    for snapshot in stream_with_missing_index_fallback(
        indexed_query=day_key_query,
        fallback_query=day_key_fallback_query,
        logger=logger,
        query_name="habit_signals.day_key_range",
        extra={"computed_at": computed_at.isoformat()},
    ):
        snapshots_by_id[snapshot.id] = dict(snapshot.to_dict() or {})

    timestamp_query = (
        meals_collection.where(filter=FieldFilter("deleted", "==", False))
        .where(filter=FieldFilter("timestamp", ">=", start_ts))
        .where(filter=FieldFilter("timestamp", "<", end_ts))
    )
    timestamp_fallback_query = (
        meals_collection.where(filter=FieldFilter("timestamp", ">=", start_ts))
        .where(filter=FieldFilter("timestamp", "<", end_ts))
    )
    for snapshot in stream_with_missing_index_fallback(
        indexed_query=timestamp_query,
        fallback_query=timestamp_fallback_query,
        logger=logger,
        query_name="habit_signals.timestamp_range",
        extra={"computed_at": computed_at.isoformat()},
    ):
        snapshots_by_id.setdefault(snapshot.id, dict(snapshot.to_dict() or {}))

    return list(snapshots_by_id.values())


def compute_habit_signals(
    *,
    profile: dict[str, Any] | None,
    meals: list[dict[str, Any]],
    computed_at: datetime | None = None,
) -> HabitSignalsResponse:
    normalized_now = (computed_at or utc_now()).astimezone(UTC)
    aggregates = _aggregate_days(meals, computed_at=normalized_now)

    day_keys_7 = _sorted_day_keys_for_window(
        aggregates,
        computed_at=normalized_now,
        window_days=RECENT_ACTIVITY_WINDOW_DAYS,
    )
    day_keys_14 = _sorted_day_keys_for_window(
        aggregates,
        computed_at=normalized_now,
        window_days=ADHERENCE_WINDOW_DAYS,
    )
    day_keys_28 = _sorted_day_keys_for_window(
        aggregates,
        computed_at=normalized_now,
        window_days=CONSISTENCY_WINDOW_DAYS,
    )

    logging_days_7 = len(day_keys_7)
    logging_days_14 = len(day_keys_14)
    logging_days_28 = len(day_keys_28)
    valid_day_keys_7 = [
        day_key for day_key in day_keys_7 if aggregates[day_key].valid_meal_count > 0
    ]
    valid_day_keys_14 = [
        day_key for day_key in day_keys_14 if aggregates[day_key].valid_meal_count > 0
    ]
    valid_day_keys_28 = [
        day_key for day_key in day_keys_28 if aggregates[day_key].valid_meal_count > 0
    ]
    valid_logging_days_7 = len(valid_day_keys_7)
    valid_logging_days_14 = len(valid_day_keys_14)
    valid_logging_days_28 = len(valid_day_keys_28)

    total_meals_14 = sum(aggregates[day_key].meal_count for day_key in day_keys_14)
    total_valid_meals_14 = sum(
        aggregates[day_key].valid_meal_count for day_key in valid_day_keys_14
    )
    avg_meals_per_logged_day_14 = (
        round_metric(total_meals_14 / logging_days_14) if logging_days_14 else 0.0
    )
    avg_valid_meals_per_valid_logged_day_14 = (
        round_metric(total_valid_meals_14 / valid_logging_days_14)
        if valid_logging_days_14
        else 0.0
    )
    logging_consistency_28 = round_metric(logging_days_28 / CONSISTENCY_WINDOW_DAYS)
    valid_logging_consistency_28 = round_metric(
        valid_logging_days_28 / CONSISTENCY_WINDOW_DAYS
    )

    calorie_target = parse_target_kcal(profile or {})
    protein_target = _extract_protein_target(profile)
    kcal_adherence_14: float | None = None
    kcal_under_target_ratio_14: float | None = None

    if calorie_target > 0 and valid_logging_days_14 > 0:
        kcal_ratios = [
            aggregates[day_key].kcal / calorie_target for day_key in valid_day_keys_14
        ]
        kcal_adherence_14 = round_metric(sum(kcal_ratios) / len(kcal_ratios))
        kcal_under_target_days = sum(
            1
            for day_key in valid_day_keys_14
            if aggregates[day_key].kcal < calorie_target * 0.9
        )
        kcal_under_target_ratio_14 = round_metric(
            kcal_under_target_days / valid_logging_days_14
        )

    protein_days_hit_14 = _build_protein_days_hit(
        day_keys_14=valid_day_keys_14,
        aggregates=aggregates,
        protein_target=protein_target,
    )
    days_with_unknown_details_14 = sum(
        1 for day_key in day_keys_14 if aggregates[day_key].has_unknown_details
    )
    days_using_timestamp_day_fallback_14 = sum(
        1
        for day_key in day_keys_14
        if aggregates[day_key].used_timestamp_day_fallback
    )
    days_using_timestamp_timing_fallback_14 = sum(
        1
        for day_key in day_keys_14
        if aggregates[day_key].used_timestamp_timing_fallback
    )

    top_risk, coach_priority = _derive_top_risk_and_priority(
        valid_logging_days_7=valid_logging_days_7,
        valid_logging_consistency_28=valid_logging_consistency_28,
        days_with_unknown_details_14=days_with_unknown_details_14,
        logged_days_14=logging_days_14,
        kcal_under_target_ratio_14=kcal_under_target_ratio_14,
        protein_days_hit_14=protein_days_hit_14,
    )

    return HabitSignalsResponse(
        computedAt=normalized_now.isoformat().replace("+00:00", "Z"),
        behavior=HabitBehavior(
            loggingDays7=logging_days_7,
            validLoggingDays7=valid_logging_days_7,
            loggingConsistency28=logging_consistency_28,
            validLoggingConsistency28=valid_logging_consistency_28,
            avgMealsPerLoggedDay14=avg_meals_per_logged_day_14,
            avgValidMealsPerValidLoggedDay14=avg_valid_meals_per_valid_logged_day_14,
            mealTypeCoverage14=_build_meal_type_coverage(valid_day_keys_14, aggregates),
            mealTypeFrequency14=_build_meal_type_frequency(valid_day_keys_14, aggregates),
            dayCoverage14=DayCoverage14(
                loggedDays=logging_days_14,
                validLoggedDays=valid_logging_days_14,
            ),
            kcalAdherence14=kcal_adherence_14,
            kcalUnderTargetRatio14=kcal_under_target_ratio_14,
            proteinDaysHit14=protein_days_hit_14,
            timingPatterns14=_build_timing_patterns(day_keys_14, aggregates),
        ),
        dataQuality=HabitDataQuality(
            daysWithUnknownMealDetails14=days_with_unknown_details_14,
            daysUsingTimestampDayFallback14=days_using_timestamp_day_fallback_14,
            daysUsingTimestampTimingFallback14=days_using_timestamp_timing_fallback_14,
        ),
        topRisk=top_risk,
        coachPriority=coach_priority,
    )


async def get_habit_signals(
    user_id: str,
    *,
    computed_at: datetime | None = None,
) -> HabitSignalsResponse:
    client: firestore.Client = get_firestore()
    user_ref = client.collection(USERS_COLLECTION).document(user_id)

    try:
        user_snapshot = user_ref.get()
        profile = dict(user_snapshot.to_dict() or {}) if user_snapshot.exists else None
        meals = _load_recent_meals(
            user_ref,
            computed_at=(computed_at or utc_now()).astimezone(UTC),
        )
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to compute habit signals.",
            extra={"user_id": user_id},
        )
        raise FirestoreServiceError("Failed to compute habit signals.") from exc

    return compute_habit_signals(
        profile=profile,
        meals=meals,
        computed_at=computed_at,
    )
