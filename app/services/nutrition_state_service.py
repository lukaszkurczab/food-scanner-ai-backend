"""Canonical day nutrition state contract for backend consumers."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import logging
from typing import Any, cast

from firebase_admin.exceptions import FirebaseError
from google.api_core.exceptions import GoogleAPICallError, RetryError
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from app.core.coercion import round_metric
from app.core.datetime_utils import utc_now
from app.core.exceptions import FirestoreServiceError
from app.core.firestore_constants import MEALS_SUBCOLLECTION, USERS_COLLECTION
from app.core.firestore_query_fallback import stream_with_missing_index_fallback
from app.db.firebase import get_firestore
from app.schemas.nutrition_state import (
    NutritionAiSummary,
    NutritionComponentState,
    NutritionComponentStatus,
    NutritionConsumed,
    NutritionHabitsSummary,
    NutritionOverTarget,
    NutritionQuality,
    NutritionRemaining,
    NutritionStateMeta,
    NutritionStateResponse,
    NutritionStreakSummary,
    NutritionTargets,
)
from app.services import ai_credits_service
from app.services.habit_signal_service import (
    CONSISTENCY_WINDOW_DAYS,
    READ_WINDOW_BUFFER_DAYS,
    _derive_day_key,
    _extract_totals,
    _is_unknown_meal_details,
    compute_habit_signals,
)
from app.services.streak_service import get_streak

logger = logging.getLogger(__name__)
UTC = timezone.utc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _coerce_target(value: object) -> float | None:
    if isinstance(value, (int, float)) and float(value) > 0:
        return float(value)
    return None


def _to_float_or_zero(value: object) -> float:
    try:
        return float(cast(Any, value))
    except (TypeError, ValueError):
        return 0.0


def _as_object_map(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    raw_map = cast(dict[object, object], value)
    result: dict[str, object] = {}
    for raw_key, raw_item in raw_map.items():
        if isinstance(raw_key, str):
            result[raw_key] = raw_item
    return result


# ---------------------------------------------------------------------------
# Bounded meal reading — replaces the previous unbounded .stream()
# ---------------------------------------------------------------------------

def _serialize_day_start(day_value: date) -> str:
    return datetime.combine(day_value, time.min, tzinfo=UTC).isoformat().replace("+00:00", "Z")


def _load_bounded_meals(
    user_ref: firestore.DocumentReference,
    *,
    reference_day_key: str,
    window_days: int = CONSISTENCY_WINDOW_DAYS,
    buffer_days: int = READ_WINDOW_BUFFER_DAYS,
) -> list[dict[str, Any]]:
    """Load meals within a bounded time window using dual-query strategy.

    Uses the same approach as habit_signal_service._load_recent_meals:
    1. Query by canonical dayKey range (primary source of truth)
    2. Query by timestamp range (fallback for legacy records without dayKey)
    3. Deduplicate by document ID

    The window is centered on ``reference_day_key`` going back ``window_days``,
    with a small buffer on both sides to handle timezone edge cases.
    """
    reference_date = datetime.strptime(reference_day_key, "%Y-%m-%d").date()
    start_day = reference_date - timedelta(days=window_days - 1)
    buffered_start = start_day - timedelta(days=buffer_days)
    buffered_end = reference_date + timedelta(days=buffer_days)

    start_day_key = buffered_start.isoformat()
    end_day_key = buffered_end.isoformat()
    start_ts = _serialize_day_start(buffered_start)
    end_ts = _serialize_day_start(buffered_end + timedelta(days=1))

    meals_collection = user_ref.collection(MEALS_SUBCOLLECTION)
    snapshots_by_id: dict[str, dict[str, Any]] = {}

    # Primary: read by canonical dayKey with the deleted filter pushed into
    # Firestore. If the composite index is missing, retry with the same bounded
    # range and filter deleted meals in memory later.
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
        query_name="nutrition_state.day_key_range",
        extra={"reference_day_key": reference_day_key},
    ):
        snapshots_by_id[snapshot.id] = dict(snapshot.to_dict() or {})

    # Fallback: read by timestamp for legacy records without valid dayKey.
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
        query_name="nutrition_state.timestamp_range",
        extra={"reference_day_key": reference_day_key},
    ):
        snapshots_by_id.setdefault(snapshot.id, dict(snapshot.to_dict() or {}))

    return list(snapshots_by_id.values())


# ---------------------------------------------------------------------------
# Macro targets
# ---------------------------------------------------------------------------


def _extract_macro_targets(profile: dict[str, Any] | None) -> dict[str, float | None]:
    profile_map = _as_object_map(profile) or {}
    macro_map = _as_object_map(profile_map.get("macroTargets")) or {}

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


# ---------------------------------------------------------------------------
# Consumed / remaining / quality
# ---------------------------------------------------------------------------


def _sum_consumed(meals: list[dict[str, Any]]) -> NutritionConsumed:
    kcal = 0.0
    protein = 0.0
    carbs = 0.0
    fat = 0.0

    for meal in meals:
        meal_kcal, meal_protein = _extract_totals(meal)
        kcal += meal_kcal
        protein += meal_protein
        totals_map = _as_object_map(meal.get("totals"))
        if totals_map is not None:
            carbs += _to_float_or_zero(totals_map.get("carbs"))
            fat += _to_float_or_zero(totals_map.get("fat"))
        else:
            ingredients = meal.get("ingredients")
            if isinstance(ingredients, list):
                ingredients_list = cast(list[object], ingredients)
                for ingredient in ingredients_list:
                    ingredient_map = _as_object_map(ingredient)
                    if ingredient_map is not None:
                        carbs += _to_float_or_zero(ingredient_map.get("carbs"))
                        fat += _to_float_or_zero(ingredient_map.get("fat"))

    return NutritionConsumed(
        kcal=round_metric(kcal, 2),
        protein=round_metric(protein, 2),
        carbs=round_metric(carbs, 2),
        fat=round_metric(fat, 2),
    )


def _build_remaining(
    *,
    targets: NutritionTargets,
    consumed: NutritionConsumed,
) -> NutritionRemaining:
    # Overshoot is clamped to zero — the UI shows 0 kcal remaining rather than
    # negative values.  If the product later wants to surface overshoot, this
    # clamp should be removed and the mobile consumer updated accordingly.
    return NutritionRemaining(
        kcal=max(round_metric(targets.kcal - consumed.kcal, 2), 0) if targets.kcal is not None else None,
        protein=max(round_metric(targets.protein - consumed.protein, 2), 0)
        if targets.protein is not None
        else None,
        carbs=max(round_metric(targets.carbs - consumed.carbs, 2), 0)
        if targets.carbs is not None
        else None,
        fat=max(round_metric(targets.fat - consumed.fat, 2), 0) if targets.fat is not None else None,
    )


def _build_over_target(
    *,
    targets: NutritionTargets,
    consumed: NutritionConsumed,
) -> NutritionOverTarget:
    return NutritionOverTarget(
        kcal=max(round_metric(consumed.kcal - targets.kcal, 2), 0) if targets.kcal is not None else None,
        protein=max(round_metric(consumed.protein - targets.protein, 2), 0)
        if targets.protein is not None
        else None,
        carbs=max(round_metric(consumed.carbs - targets.carbs, 2), 0)
        if targets.carbs is not None
        else None,
        fat=max(round_metric(consumed.fat - targets.fat, 2), 0) if targets.fat is not None else None,
    )


def _build_quality(meals: list[dict[str, Any]]) -> NutritionQuality:
    meals_logged = len(meals)
    missing_nutrition_meals = sum(1 for meal in meals if _is_unknown_meal_details(meal))
    completeness = (
        round_metric((meals_logged - missing_nutrition_meals) / meals_logged)
        if meals_logged > 0
        else 0.0
    )
    return NutritionQuality(
        mealsLogged=meals_logged,
        missingNutritionMeals=missing_nutrition_meals,
        dataCompletenessScore=completeness,
    )


# ---------------------------------------------------------------------------
# Meal filtering
# ---------------------------------------------------------------------------


def _filter_core_meals(meals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Safety net — bounded queries already filter deleted == False at Firestore
    level, but in-memory filter guards against any edge-case leak."""
    return [meal for meal in meals if not bool(meal.get("deleted"))]


def _filter_meals_for_day(meals: list[dict[str, Any]], *, day_key: str) -> list[dict[str, Any]]:
    return [meal for meal in meals if _derive_day_key(meal) == day_key]


# ---------------------------------------------------------------------------
# Sub-summaries: habits, streak, AI
# ---------------------------------------------------------------------------


def build_habits_summary(
    *,
    profile: dict[str, Any] | None,
    meals: list[dict[str, Any]],
    reference_day_key: str,
) -> NutritionHabitsSummary:
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


async def build_streak_summary(user_id: str) -> NutritionStreakSummary:
    """Read the authoritative streak document instead of recomputing from meals.

    The streak document is kept in sync by streak_service.sync_streak_from_meals()
    which is called on every meal upsert/delete.  Reading the document directly
    avoids an unbounded history scan and is always consistent with the last
    write.
    """
    streak = await get_streak(user_id)
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


def _build_state_meta(
    *,
    habits_status: NutritionComponentState,
    streak_status: NutritionComponentState,
    ai_status: NutritionComponentState,
) -> NutritionStateMeta:
    return NutritionStateMeta(
        isDegraded=any(status == "error" for status in (habits_status, streak_status, ai_status)),
        componentStatus=NutritionComponentStatus(
            habits=habits_status,
            streak=streak_status,
            ai=ai_status,
        ),
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def get_nutrition_state(
    user_id: str,
    *,
    day_key: str | None = None,
    now: datetime | None = None,
) -> NutritionStateResponse:
    computed_at = (now or utc_now()).astimezone(UTC)
    resolved_day_key = resolve_requested_day_key(day_key, now=computed_at)

    client: firestore.Client = get_firestore()
    user_ref = client.collection(USERS_COLLECTION).document(user_id)

    try:
        user_snapshot = user_ref.get()
        profile = dict(user_snapshot.to_dict() or {}) if user_snapshot.exists else None

        # Bounded read: load only meals within the habit window (28 + 1 buffer
        # days), centered on the requested day — NOT the full meal history.
        # This covers both the requested day's consumed/quality data and the
        # 28-day lookback for habit signals.
        bounded_meals = _load_bounded_meals(
            user_ref,
            reference_day_key=resolved_day_key,
        )
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to build nutrition state.",
            extra={"user_id": user_id, "day_key": resolved_day_key},
        )
        raise FirestoreServiceError("Failed to build nutrition state.") from exc

    # Safety net: the preferred query path filters deleted == False at
    # Firestore level, while the missing-index fallback filters in memory.
    meals = _filter_core_meals(bounded_meals)
    requested_day_meals = _filter_meals_for_day(meals, day_key=resolved_day_key)
    targets_map = _extract_macro_targets(profile)
    targets = NutritionTargets(**targets_map)
    consumed = _sum_consumed(requested_day_meals)
    remaining = _build_remaining(targets=targets, consumed=consumed)
    over_target = _build_over_target(targets=targets, consumed=consumed)
    quality = _build_quality(requested_day_meals)
    habits_status = "ok"
    streak_status = "ok"
    ai_status = "ok"

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
        habits_status = "error"

    # Streak: read the authoritative streak document directly instead of
    # recomputing from unbounded meal history.
    try:
        streak = await build_streak_summary(user_id)
    except Exception:
        logger.exception(
            "Failed to include streak summary in nutrition state.",
            extra={"user_id": user_id, "day_key": resolved_day_key},
        )
        streak = _default_streak_summary()
        streak_status = "error"

    try:
        ai = await build_ai_summary(user_id)
    except Exception:
        logger.exception(
            "Failed to include AI summary in nutrition state.",
            extra={"user_id": user_id, "day_key": resolved_day_key},
        )
        ai = _default_ai_summary()
        ai_status = "error"

    return NutritionStateResponse(
        computedAt=_serialize_datetime(computed_at),
        dayKey=resolved_day_key,
        targets=targets,
        consumed=consumed,
        remaining=remaining,
        overTarget=over_target,
        quality=quality,
        habits=habits,
        streak=streak,
        ai=ai,
        meta=_build_state_meta(
            habits_status=habits_status,
            streak_status=streak_status,
            ai_status=ai_status,
        ),
    )
