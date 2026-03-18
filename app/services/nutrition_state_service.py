"""Canonical day nutrition state contract for backend consumers."""

from __future__ import annotations

from datetime import UTC, datetime, time
import logging
from typing import Any

from firebase_admin.exceptions import FirebaseError
from google.api_core.exceptions import GoogleAPICallError, RetryError
from google.cloud import firestore

from app.core.config import settings
from app.core.datetime_utils import utc_now
from app.core.exceptions import FirestoreServiceError, StateDisabledError
from app.db.firebase import get_firestore
from app.schemas.nutrition_state import (
    NutritionAiSummary,
    NutritionConsumed,
    NutritionHabitsSummary,
    NutritionQuality,
    NutritionRemaining,
    NutritionStateResponse,
    NutritionStreakSummary,
    NutritionTargets,
)
from app.services import ai_credits_service
from app.services.habit_signal_service import (
    _derive_day_key,
    _extract_totals,
    _is_unknown_meal_details,
    compute_habit_signals,
)
from app.services.streak_service import _build_streak_state_from_meals, _parse_target_kcal

logger = logging.getLogger(__name__)

USERS_COLLECTION = "users"
MEALS_SUBCOLLECTION = "meals"


def _serialize_datetime(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_day_key_or_raise(value: str) -> str:
    normalized = value.strip()
    try:
        return datetime.strptime(normalized, "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise ValueError("Invalid day key. Expected YYYY-MM-DD.") from exc


def resolve_requested_day_key(day_key: str | None, *, now: datetime | None = None) -> str:
    if day_key is not None:
        return _parse_day_key_or_raise(day_key)
    return (now or utc_now()).astimezone(UTC).date().isoformat()


def _round_metric(value: float, digits: int = 4) -> float:
    return round(float(value), digits)


def _coerce_target(value: object) -> float | None:
    if isinstance(value, (int, float)) and float(value) > 0:
        return float(value)
    return None


def _extract_macro_targets(profile: dict[str, Any] | None) -> dict[str, float | None]:
    profile_map = profile if isinstance(profile, dict) else {}
    macro_targets = profile_map.get("macroTargets")
    macro_map = macro_targets if isinstance(macro_targets, dict) else {}

    return {
        "kcal": _coerce_target(profile_map.get("calorieTarget"))
        or _coerce_target(profile_map.get("targetKcal")),
        "protein": _coerce_target(profile_map.get("proteinTarget"))
        or _coerce_target(profile_map.get("targetProtein"))
        or _coerce_target(profile_map.get("proteinGoal"))
        or _coerce_target(macro_map.get("proteinGrams"))
        or _coerce_target(macro_map.get("protein")),
        "carbs": _coerce_target(macro_map.get("carbsGrams"))
        or _coerce_target(macro_map.get("carbs")),
        "fat": _coerce_target(macro_map.get("fatGrams"))
        or _coerce_target(macro_map.get("fat")),
    }


def _sum_consumed(meals: list[dict[str, Any]]) -> NutritionConsumed:
    kcal = 0.0
    protein = 0.0
    carbs = 0.0
    fat = 0.0

    for meal in meals:
        meal_kcal, meal_protein = _extract_totals(meal)
        kcal += meal_kcal
        protein += meal_protein
        totals = meal.get("totals")
        if isinstance(totals, dict):
            carbs += float(totals.get("carbs") or 0)
            fat += float(totals.get("fat") or 0)
        else:
            ingredients = meal.get("ingredients")
            if isinstance(ingredients, list):
                for ingredient in ingredients:
                    if isinstance(ingredient, dict):
                        carbs += float(ingredient.get("carbs") or 0)
                        fat += float(ingredient.get("fat") or 0)

    return NutritionConsumed(
        kcal=_round_metric(kcal, 2),
        protein=_round_metric(protein, 2),
        carbs=_round_metric(carbs, 2),
        fat=_round_metric(fat, 2),
    )


def _build_remaining(
    *,
    targets: NutritionTargets,
    consumed: NutritionConsumed,
) -> NutritionRemaining:
    return NutritionRemaining(
        kcal=max(_round_metric(targets.kcal - consumed.kcal, 2), 0) if targets.kcal is not None else None,
        protein=max(_round_metric(targets.protein - consumed.protein, 2), 0)
        if targets.protein is not None
        else None,
        carbs=max(_round_metric(targets.carbs - consumed.carbs, 2), 0)
        if targets.carbs is not None
        else None,
        fat=max(_round_metric(targets.fat - consumed.fat, 2), 0) if targets.fat is not None else None,
    )


def _build_quality(meals: list[dict[str, Any]]) -> NutritionQuality:
    meals_logged = len(meals)
    missing_nutrition_meals = sum(1 for meal in meals if _is_unknown_meal_details(meal))
    completeness = (
        _round_metric((meals_logged - missing_nutrition_meals) / meals_logged)
        if meals_logged > 0
        else 0.0
    )
    return NutritionQuality(
        mealsLogged=meals_logged,
        missingNutritionMeals=missing_nutrition_meals,
        dataCompletenessScore=completeness,
    )


def _filter_core_meals(meals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [meal for meal in meals if not bool(meal.get("deleted"))]


def _filter_meals_for_day(meals: list[dict[str, Any]], *, day_key: str) -> list[dict[str, Any]]:
    return [meal for meal in meals if _derive_day_key(meal) == day_key]


def build_habits_summary(
    *,
    profile: dict[str, Any] | None,
    meals: list[dict[str, Any]],
    reference_day_key: str,
) -> NutritionHabitsSummary:
    if not settings.HABITS_ENABLED:
        return NutritionHabitsSummary()

    reference_dt = datetime.combine(
        datetime.strptime(reference_day_key, "%Y-%m-%d").date(),
        time(hour=12),
        tzinfo=UTC,
    )
    signals = compute_habit_signals(
        profile=profile,
        meals=meals,
        computed_at=reference_dt,
    )
    return NutritionHabitsSummary(
        available=True,
        behavior=signals.behavior,
        dataQuality=signals.dataQuality,
        topRisk=signals.topRisk,
        coachPriority=signals.coachPriority,
    )


def _build_daily_kcal_by_day(
    meals: list[dict[str, Any]],
    *,
    reference_day_key: str,
) -> dict[str, float]:
    daily_kcal: dict[str, float] = {}
    reference_day = datetime.strptime(reference_day_key, "%Y-%m-%d").date()

    for meal in meals:
        day_key = _derive_day_key(meal)
        if day_key is None:
            continue
        parsed_day = datetime.strptime(day_key, "%Y-%m-%d").date()
        if parsed_day > reference_day:
            continue
        meal_kcal, _meal_protein = _extract_totals(meal)
        daily_kcal[day_key] = daily_kcal.get(day_key, 0.0) + meal_kcal

    return daily_kcal


def build_streak_summary(
    *,
    profile: dict[str, Any] | None,
    meals: list[dict[str, Any]],
    reference_day_key: str,
) -> NutritionStreakSummary:
    target_kcal = _parse_target_kcal(profile or {})
    streak = _build_streak_state_from_meals(
        daily_kcal=_build_daily_kcal_by_day(meals, reference_day_key=reference_day_key),
        target_kcal=target_kcal,
        threshold_pct=0.8,
        reference_day_key=reference_day_key,
    )
    current_raw = streak.get("current")
    last_date_raw = streak.get("lastDate")
    return NutritionStreakSummary(
        available=True,
        current=current_raw if isinstance(current_raw, int) and current_raw >= 0 else 0,
        lastDate=last_date_raw if isinstance(last_date_raw, str) else None,
    )


async def build_ai_summary(user_id: str) -> NutritionAiSummary:
    credits = await ai_credits_service.get_credits_status(user_id)
    return NutritionAiSummary(
        available=True,
        tier=credits.tier,
        balance=credits.balance,
        allocation=credits.allocation,
        usedThisPeriod=max(credits.allocation - credits.balance, 0),
        periodStartAt=_serialize_datetime(credits.periodStartAt),
        periodEndAt=_serialize_datetime(credits.periodEndAt),
        costs=credits.costs,
    )


def _default_habits_summary() -> NutritionHabitsSummary:
    return NutritionHabitsSummary()


def _default_streak_summary() -> NutritionStreakSummary:
    return NutritionStreakSummary()


def _default_ai_summary() -> NutritionAiSummary:
    return NutritionAiSummary()


async def get_nutrition_state(
    user_id: str,
    *,
    day_key: str | None = None,
    now: datetime | None = None,
) -> NutritionStateResponse:
    if not settings.STATE_ENABLED:
        raise StateDisabledError("Nutrition state computation is disabled")

    computed_at = (now or utc_now()).astimezone(UTC)
    resolved_day_key = resolve_requested_day_key(day_key, now=computed_at)

    client: firestore.Client = get_firestore()
    user_ref = client.collection(USERS_COLLECTION).document(user_id)

    try:
        user_snapshot = user_ref.get()
        profile = dict(user_snapshot.to_dict() or {}) if user_snapshot.exists else None
        all_meals = [
            dict(snapshot.to_dict() or {})
            for snapshot in user_ref.collection(MEALS_SUBCOLLECTION).stream()
        ]
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to build nutrition state.",
            extra={"user_id": user_id, "day_key": resolved_day_key},
        )
        raise FirestoreServiceError("Failed to build nutrition state.") from exc

    meals = _filter_core_meals(all_meals)
    requested_day_meals = _filter_meals_for_day(meals, day_key=resolved_day_key)
    targets_map = _extract_macro_targets(profile)
    targets = NutritionTargets(**targets_map)
    consumed = _sum_consumed(requested_day_meals)
    remaining = _build_remaining(targets=targets, consumed=consumed)
    quality = _build_quality(requested_day_meals)

    try:
        habits = build_habits_summary(
            profile=profile,
            meals=meals,
            reference_day_key=resolved_day_key,
        )
    except Exception:
        logger.exception(
            "Failed to include habits summary in nutrition state.",
            extra={"user_id": user_id, "day_key": resolved_day_key},
        )
        habits = _default_habits_summary()

    try:
        streak = build_streak_summary(
            profile=profile,
            meals=meals,
            reference_day_key=resolved_day_key,
        )
    except Exception:
        logger.exception(
            "Failed to include streak summary in nutrition state.",
            extra={"user_id": user_id, "day_key": resolved_day_key},
        )
        streak = _default_streak_summary()

    try:
        ai = await build_ai_summary(user_id)
    except Exception:
        logger.exception(
            "Failed to include AI summary in nutrition state.",
            extra={"user_id": user_id, "day_key": resolved_day_key},
        )
        ai = _default_ai_summary()

    return NutritionStateResponse(
        computedAt=_serialize_datetime(computed_at),
        dayKey=resolved_day_key,
        targets=targets,
        consumed=consumed,
        remaining=remaining,
        quality=quality,
        habits=habits,
        streak=streak,
        ai=ai,
    )
