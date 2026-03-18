"""Behavioral signal derivation from user meal history."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
import logging
from typing import Any

from firebase_admin.exceptions import FirebaseError
from google.api_core.exceptions import GoogleAPICallError, RetryError
from google.cloud import firestore

from app.core.config import settings
from app.core.datetime_utils import parse_flexible_datetime, utc_now
from app.core.exceptions import FirestoreServiceError, HabitsDisabledError
from app.db.firebase import get_firestore
from app.schemas.habits import (
    CoachPriority,
    HabitBehavior,
    HabitDataQuality,
    HabitSignalsResponse,
    MealTypeCoverage14,
    ProteinDaysHit14,
    TopRisk,
)
from app.services.streak_service import _parse_target_kcal

logger = logging.getLogger(__name__)

USERS_COLLECTION = "users"
MEALS_SUBCOLLECTION = "meals"
VALID_MEAL_TYPES = ("breakfast", "lunch", "dinner", "snack", "other")
LOW_CONFIDENCE_THRESHOLD = 0.5
RECENT_ACTIVITY_WINDOW_DAYS = 7
ADHERENCE_WINDOW_DAYS = 14
CONSISTENCY_WINDOW_DAYS = 28


@dataclass(frozen=True)
class NormalizedMeal:
    day_key: str
    meal_type: str
    kcal: float
    protein: float
    unknown_details: bool


@dataclass
class DailyAggregate:
    meal_count: int = 0
    kcal: float = 0.0
    protein: float = 0.0
    meal_types: set[str] = field(default_factory=set)
    has_unknown_details: bool = False


def _round_metric(value: float, digits: int = 4) -> float:
    return round(float(value), digits)


def _to_date(day_key: str) -> date | None:
    try:
        return datetime.strptime(day_key, "%Y-%m-%d").date()
    except ValueError:
        return None


def _derive_day_key(raw_meal: dict[str, Any]) -> str | None:
    day_key = raw_meal.get("dayKey")
    if isinstance(day_key, str):
        normalized_day_key = day_key.strip()
        if _to_date(normalized_day_key) is not None:
            return normalized_day_key

    timestamp = parse_flexible_datetime(raw_meal.get("timestamp"))
    if timestamp is None:
        return None
    return timestamp.date().isoformat()


def _coerce_number(value: object, *, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def _extract_totals(raw_meal: dict[str, Any]) -> tuple[float, float]:
    totals = raw_meal.get("totals")
    if isinstance(totals, dict):
        kcal = _coerce_number(totals.get("kcal"))
        protein = _coerce_number(totals.get("protein"))
        if kcal > 0 or protein > 0:
            return kcal, protein

    kcal = 0.0
    protein = 0.0
    ingredients = raw_meal.get("ingredients")
    if isinstance(ingredients, list):
        for raw_ingredient in ingredients:
            if not isinstance(raw_ingredient, dict):
                continue
            kcal += _coerce_number(raw_ingredient.get("kcal"))
            protein += _coerce_number(raw_ingredient.get("protein"))
    return kcal, protein


def _has_meaningful_ingredients(raw_meal: dict[str, Any]) -> bool:
    ingredients = raw_meal.get("ingredients")
    if not isinstance(ingredients, list):
        return False

    for raw_ingredient in ingredients:
        if not isinstance(raw_ingredient, dict):
            continue
        if isinstance(raw_ingredient.get("name"), str) and raw_ingredient["name"].strip():
            return True
        if any(
            _coerce_number(raw_ingredient.get(field_name)) > 0
            for field_name in ("amount", "kcal", "protein", "carbs", "fat")
        ):
            return True
    return False


def _has_meaningful_totals(raw_meal: dict[str, Any]) -> bool:
    kcal, protein = _extract_totals(raw_meal)
    if kcal > 0 or protein > 0:
        return True

    totals = raw_meal.get("totals")
    if not isinstance(totals, dict):
        return False
    return any(
        _coerce_number(totals.get(field_name)) > 0
        for field_name in ("carbs", "fat")
    )


def _parse_ai_meta(raw_meal: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("aiMeta", "ai_meta"):
        value = raw_meal.get(key)
        if isinstance(value, dict):
            return value
    return None


def _has_low_confidence_ai_meta(raw_meal: dict[str, Any]) -> bool:
    ai_meta = _parse_ai_meta(raw_meal)
    if not isinstance(ai_meta, dict):
        return False
    confidence = ai_meta.get("confidence")
    return _coerce_number(confidence, default=1.0) < LOW_CONFIDENCE_THRESHOLD


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


def _normalize_meal(raw_meal: dict[str, Any]) -> NormalizedMeal | None:
    day_key = _derive_day_key(raw_meal)
    if day_key is None:
        return None

    meal_type = raw_meal.get("type")
    normalized_meal_type = meal_type if isinstance(meal_type, str) and meal_type in VALID_MEAL_TYPES else "other"
    kcal, protein = _extract_totals(raw_meal)

    return NormalizedMeal(
        day_key=day_key,
        meal_type=normalized_meal_type,
        kcal=kcal,
        protein=protein,
        unknown_details=_is_unknown_meal_details(raw_meal),
    )


def _extract_protein_target(raw_user: dict[str, Any] | None) -> float | None:
    if not isinstance(raw_user, dict):
        return None

    for key in ("proteinTarget", "targetProtein", "proteinGoal"):
        value = raw_user.get(key)
        if isinstance(value, (int, float)) and float(value) > 0:
            return float(value)

    macro_targets = raw_user.get("macroTargets")
    if isinstance(macro_targets, dict):
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
        aggregate.kcal += normalized.kcal
        aggregate.protein += normalized.protein
        aggregate.meal_types.add(normalized.meal_type)
        aggregate.has_unknown_details = aggregate.has_unknown_details or normalized.unknown_details

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
        for meal_type in aggregates[day_key].meal_types
    }
    return MealTypeCoverage14(
        breakfast="breakfast" in covered_types,
        lunch="lunch" in covered_types,
        dinner="dinner" in covered_types,
        snack="snack" in covered_types,
        other="other" in covered_types,
        coveredCount=len(covered_types),
    )


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
        ratio=_round_metric(hit_days / eligible_days) if eligible_days else None,
    )


def _derive_top_risk_and_priority(
    *,
    logging_days_7: int,
    logging_consistency_28: float,
    days_with_unknown_details_14: int,
    logged_days_14: int,
    kcal_under_target_ratio_14: float | None,
    protein_days_hit_14: ProteinDaysHit14,
) -> tuple[TopRisk, CoachPriority]:
    if logging_days_7 < 4 or logging_consistency_28 < 0.5:
        return "under_logging", "logging_foundation"

    if logged_days_14 > 0 and days_with_unknown_details_14 / logged_days_14 >= 0.4:
        return "high_unknown_meal_details", "meal_detail_quality"

    if protein_days_hit_14.ratio is not None and protein_days_hit_14.ratio < 0.5:
        return "low_protein_consistency", "protein_consistency"

    if kcal_under_target_ratio_14 is not None and kcal_under_target_ratio_14 >= 0.6:
        return "calorie_under_target", "calorie_adherence"

    return "none", "maintain"


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

    total_meals_14 = sum(aggregates[day_key].meal_count for day_key in day_keys_14)
    avg_meals_per_logged_day_14 = (
        _round_metric(total_meals_14 / logging_days_14) if logging_days_14 else 0.0
    )
    logging_consistency_28 = _round_metric(logging_days_28 / CONSISTENCY_WINDOW_DAYS)

    calorie_target = _parse_target_kcal(profile or {})
    protein_target = _extract_protein_target(profile)
    kcal_adherence_14: float | None = None
    kcal_under_target_ratio_14: float | None = None

    if calorie_target > 0 and logging_days_14 > 0:
        kcal_ratios = [aggregates[day_key].kcal / calorie_target for day_key in day_keys_14]
        kcal_adherence_14 = _round_metric(sum(kcal_ratios) / len(kcal_ratios))
        kcal_under_target_days = sum(
            1 for day_key in day_keys_14 if aggregates[day_key].kcal < calorie_target * 0.9
        )
        kcal_under_target_ratio_14 = _round_metric(kcal_under_target_days / logging_days_14)

    protein_days_hit_14 = _build_protein_days_hit(
        day_keys_14=day_keys_14,
        aggregates=aggregates,
        protein_target=protein_target,
    )
    days_with_unknown_details_14 = sum(
        1 for day_key in day_keys_14 if aggregates[day_key].has_unknown_details
    )

    top_risk, coach_priority = _derive_top_risk_and_priority(
        logging_days_7=logging_days_7,
        logging_consistency_28=logging_consistency_28,
        days_with_unknown_details_14=days_with_unknown_details_14,
        logged_days_14=logging_days_14,
        kcal_under_target_ratio_14=kcal_under_target_ratio_14,
        protein_days_hit_14=protein_days_hit_14,
    )

    return HabitSignalsResponse(
        computedAt=normalized_now.isoformat().replace("+00:00", "Z"),
        behavior=HabitBehavior(
            loggingDays7=logging_days_7,
            loggingConsistency28=logging_consistency_28,
            avgMealsPerLoggedDay14=avg_meals_per_logged_day_14,
            mealTypeCoverage14=_build_meal_type_coverage(day_keys_14, aggregates),
            kcalAdherence14=kcal_adherence_14,
            kcalUnderTargetRatio14=kcal_under_target_ratio_14,
            proteinDaysHit14=protein_days_hit_14,
        ),
        dataQuality=HabitDataQuality(
            daysWithUnknownMealDetails14=days_with_unknown_details_14,
        ),
        topRisk=top_risk,
        coachPriority=coach_priority,
    )


async def get_habit_signals(
    user_id: str,
    *,
    computed_at: datetime | None = None,
) -> HabitSignalsResponse:
    if not settings.HABITS_ENABLED:
        raise HabitsDisabledError("Habit signal computation is disabled")

    client: firestore.Client = get_firestore()
    user_ref = client.collection(USERS_COLLECTION).document(user_id)

    try:
        user_snapshot = user_ref.get()
        profile = dict(user_snapshot.to_dict() or {}) if user_snapshot.exists else None
        meals = [dict(snapshot.to_dict() or {}) for snapshot in user_ref.collection(MEALS_SUBCOLLECTION).stream()]
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
