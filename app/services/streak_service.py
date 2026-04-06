"""Backend-owned streak write model and streak badge awarding."""

from datetime import datetime, timezone
import logging
import re
from typing import TypeAlias

from firebase_admin.exceptions import FirebaseError
from google.api_core.exceptions import GoogleAPICallError, RetryError
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from app.core.exceptions import FirestoreServiceError
from app.core.firestore_constants import (
    BADGES_SUBCOLLECTION,
    MEALS_SUBCOLLECTION,
    STREAK_SUBCOLLECTION,
    USERS_COLLECTION,
)
from app.db.firebase import get_firestore
from app.services.nutrition_target_service import parse_target_kcal

logger = logging.getLogger(__name__)

DAY_KEY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
STREAK_DOCUMENT_ID = "main"
STREAK_MILESTONES = (7, 30, 90, 180, 365, 500, 1000)
STREAK_BADGE_SPECS = {
    7: {"id": "streak_7", "color": "#5AA469"},
    30: {"id": "streak_30", "color": "#4A90E2"},
    90: {"id": "streak_90", "color": "#C9A227"},
    180: {"id": "streak_180", "color": "#9C27B0"},
    365: {"id": "streak_365", "color": "#B0BEC5"},
    500: {"id": "streak_500", "color": "#90CAF9"},
    1000: {"id": "streak_1000", "color": "#80DEEA"},
}


StreakState: TypeAlias = dict[str, object]
INIT_STREAK: StreakState = {"current": 0, "lastDate": None}


class StreakValidationError(Exception):
    """Raised when streak endpoint payload is invalid."""


def _validate_day_key(day_key: str) -> None:
    if not DAY_KEY_RE.match(day_key):
        raise StreakValidationError("Invalid day key.")


def _utc_day_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _sanitize_streak_doc(raw: object) -> StreakState | None:
    if not isinstance(raw, dict):
        return None

    current = raw.get("current")
    last_date = raw.get("lastDate")
    normalized_current = current if isinstance(current, int) and current >= 0 else None
    normalized_last_date = (
        last_date
        if last_date is None or (isinstance(last_date, str) and DAY_KEY_RE.match(last_date))
        else None
    )
    if normalized_current is None or normalized_last_date is None and last_date is not None:
        return None
    return {"current": normalized_current, "lastDate": normalized_last_date}


def _build_streak_state(current: int, last_date: str | None) -> StreakState:
    return {"current": current, "lastDate": last_date}


def _streak_current(streak: StreakState) -> int:
    current = streak.get("current")
    return current if isinstance(current, int) and current >= 0 else 0


def _streak_last_date(streak: StreakState) -> str | None:
    last_date = streak.get("lastDate")
    return last_date if isinstance(last_date, str) else None


def _missed_since_streak_day(last_date: str | None, day_key: str) -> bool:
    if not last_date:
        return True

    last_dt = datetime.strptime(last_date, "%Y-%m-%d")
    current_dt = datetime.strptime(day_key, "%Y-%m-%d")
    diff_days = (current_dt - last_dt).days
    return diff_days >= 2


def _has_reached_streak_threshold(
    *,
    todays_kcal: float,
    target_kcal: float,
    threshold_pct: float,
) -> bool:
    if target_kcal <= 0:
        return False
    return todays_kcal / target_kcal >= threshold_pct


def _streak_ref(client: firestore.Client, user_id: str) -> firestore.DocumentReference:
    return (
        client.collection(USERS_COLLECTION)
        .document(user_id)
        .collection(STREAK_SUBCOLLECTION)
        .document(STREAK_DOCUMENT_ID)
    )


def _badge_collection(
    client: firestore.Client, user_id: str
) -> firestore.CollectionReference:
    return client.collection(USERS_COLLECTION).document(user_id).collection(BADGES_SUBCOLLECTION)


def _meals_collection(
    client: firestore.Client, user_id: str
) -> firestore.CollectionReference:
    return client.collection(USERS_COLLECTION).document(user_id).collection(MEALS_SUBCOLLECTION)


def _extract_meal_day_key(raw_meal: dict[str, object]) -> str | None:
    day_key = raw_meal.get("dayKey")
    if isinstance(day_key, str) and DAY_KEY_RE.match(day_key):
        return day_key

    timestamp = raw_meal.get("timestamp")
    if isinstance(timestamp, str):
        prefix = timestamp[:10]
        if DAY_KEY_RE.match(prefix):
            return prefix
    return None


def _extract_meal_kcal(raw_meal: dict[str, object]) -> float:
    totals = raw_meal.get("totals")
    if isinstance(totals, dict):
        value = totals.get("kcal")
        if isinstance(value, (int, float)):
            return float(value)
    return 0.0


def _build_streak_state_from_meals(
    *,
    daily_kcal: dict[str, float],
    target_kcal: float,
    threshold_pct: float,
    reference_day_key: str,
) -> StreakState:
    if target_kcal <= 0:
        return _build_streak_state(0, None)

    qualified_days = sorted(
        day_key
        for day_key, consumed_kcal in daily_kcal.items()
        if _has_reached_streak_threshold(
            todays_kcal=consumed_kcal,
            target_kcal=target_kcal,
            threshold_pct=threshold_pct,
        )
    )
    if not qualified_days:
        return _build_streak_state(0, None)

    last_date = qualified_days[-1]
    last_dt = datetime.strptime(last_date, "%Y-%m-%d")
    reference_dt = datetime.strptime(reference_day_key, "%Y-%m-%d")
    if reference_dt < last_dt:
        reference_dt = last_dt
        reference_day_key = last_date

    if _missed_since_streak_day(last_date, reference_day_key):
        return _build_streak_state(0, last_date)

    qualified_set = set(qualified_days)
    current = 1
    cursor_dt = last_dt
    while True:
        previous_dt = cursor_dt.fromordinal(cursor_dt.toordinal() - 1)
        previous_key = previous_dt.strftime("%Y-%m-%d")
        if previous_key not in qualified_set:
            break
        current += 1
        cursor_dt = previous_dt

    return _build_streak_state(current, last_date)


def _normalize_streak_result(data: dict[str, object] | None) -> StreakState:
    normalized = _sanitize_streak_doc(data or {})
    return normalized if normalized is not None else _build_streak_state(0, None)


@firestore.transactional
def _ensure_streak_transaction(
    transaction: firestore.Transaction,
    document_ref: firestore.DocumentReference,
) -> StreakState:
    snapshot = document_ref.get(transaction=transaction)
    if not snapshot.exists:
        transaction.set(document_ref, INIT_STREAK)
        return _build_streak_state(0, None)

    normalized = _sanitize_streak_doc(snapshot.to_dict() or {})
    if normalized is None:
        transaction.set(document_ref, INIT_STREAK, merge=True)
        return _build_streak_state(0, None)

    return normalized


@firestore.transactional
def _reset_streak_if_missed_transaction(
    transaction: firestore.Transaction,
    document_ref: firestore.DocumentReference,
    day_key: str,
) -> StreakState:
    snapshot = document_ref.get(transaction=transaction)
    if not snapshot.exists:
        transaction.set(document_ref, INIT_STREAK)
        return _build_streak_state(0, None)

    normalized = _sanitize_streak_doc(snapshot.to_dict() or {})
    if normalized is None:
        transaction.set(document_ref, INIT_STREAK, merge=True)
        return _build_streak_state(0, None)

    last_date = _streak_last_date(normalized)
    if _missed_since_streak_day(last_date, day_key):
        reset_payload = _build_streak_state(0, last_date)
        transaction.update(document_ref, {"current": 0})
        return reset_payload

    return normalized


@firestore.transactional
def _recalculate_streak_transaction(
    transaction: firestore.Transaction,
    document_ref: firestore.DocumentReference,
    day_key: str,
    todays_kcal: float,
    target_kcal: float,
    threshold_pct: float,
) -> StreakState:
    snapshot = document_ref.get(transaction=transaction)
    current_value = _build_streak_state(0, None)

    if snapshot.exists:
        current_value = _normalize_streak_result(snapshot.to_dict())
    elif not _has_reached_streak_threshold(
        todays_kcal=todays_kcal,
        target_kcal=target_kcal,
        threshold_pct=threshold_pct,
    ):
        return current_value

    if not _has_reached_streak_threshold(
        todays_kcal=todays_kcal,
        target_kcal=target_kcal,
        threshold_pct=threshold_pct,
    ):
        return current_value

    if not snapshot.exists:
        next_value = _build_streak_state(1, day_key)
        transaction.set(document_ref, next_value)
        return next_value

    if _streak_last_date(current_value) == day_key:
        return current_value

    if _missed_since_streak_day(_streak_last_date(current_value), day_key):
        next_value = _build_streak_state(1, day_key)
        transaction.update(document_ref, next_value)
        return next_value

    next_value = _build_streak_state(_streak_current(current_value) + 1, day_key)
    transaction.update(document_ref, next_value)
    return next_value


def _award_streak_badges(
    client: firestore.Client,
    user_id: str,
    current_streak: int,
) -> list[str]:
    if current_streak <= 0:
        return []

    eligible = [
        (milestone, STREAK_BADGE_SPECS[milestone])
        for milestone in STREAK_MILESTONES
        if current_streak >= milestone
    ]
    if not eligible:
        return []

    badge_collection = _badge_collection(client, user_id)
    refs = [badge_collection.document(spec["id"]) for _, spec in eligible]
    existing_ids = {snap.id for snap in client.get_all(refs) if snap.exists}  # type: ignore[attr-defined]

    unlocked_at = int(datetime.now(timezone.utc).timestamp() * 1000)
    awarded_badges: list[str] = []
    batch = client.batch()

    for milestone, spec in eligible:
        if spec["id"] in existing_ids:
            continue
        batch.set(
            badge_collection.document(spec["id"]),
            {
                "id": spec["id"],
                "type": "streak",
                "label": f"{milestone} days streak",
                "milestone": milestone,
                "icon": "🔥",
                "color": spec["color"],
                "unlockedAt": unlocked_at,
            },
            merge=True,
        )
        awarded_badges.append(spec["id"])

    if awarded_badges:
        batch.commit()

    return awarded_badges


async def get_streak(user_id: str) -> StreakState:
    client: firestore.Client = get_firestore()
    document_ref = _streak_ref(client, user_id)

    try:
        snapshot = document_ref.get()
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception("Failed to fetch streak.", extra={"user_id": user_id})
        raise FirestoreServiceError("Failed to fetch streak.") from exc

    if not snapshot.exists:
        return _build_streak_state(0, None)

    return _normalize_streak_result(snapshot.to_dict())


async def ensure_streak(user_id: str, day_key: str) -> tuple[StreakState, list[str]]:
    _validate_day_key(day_key)
    client: firestore.Client = get_firestore()
    document_ref = _streak_ref(client, user_id)
    transaction = client.transaction()

    try:
        streak = _ensure_streak_transaction(transaction, document_ref)
        awarded = _award_streak_badges(client, user_id, _streak_current(streak))
    except StreakValidationError:
        raise
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception("Failed to ensure streak.", extra={"user_id": user_id})
        raise FirestoreServiceError("Failed to ensure streak.") from exc

    return streak, awarded


async def reset_streak_if_missed(
    user_id: str, day_key: str
) -> tuple[StreakState, list[str]]:
    _validate_day_key(day_key)
    client: firestore.Client = get_firestore()
    document_ref = _streak_ref(client, user_id)
    transaction = client.transaction()

    try:
        streak = _reset_streak_if_missed_transaction(transaction, document_ref, day_key)
        awarded = _award_streak_badges(client, user_id, _streak_current(streak))
    except StreakValidationError:
        raise
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to reset streak if missed.",
            extra={"user_id": user_id, "day_key": day_key},
        )
        raise FirestoreServiceError("Failed to reset streak.") from exc

    return streak, awarded


async def recalculate_streak(
    *,
    user_id: str,
    day_key: str,
    todays_kcal: float,
    target_kcal: float,
    threshold_pct: float,
) -> tuple[StreakState, list[str]]:
    _validate_day_key(day_key)
    client: firestore.Client = get_firestore()
    document_ref = _streak_ref(client, user_id)
    transaction = client.transaction()

    try:
        streak = _recalculate_streak_transaction(
            transaction,
            document_ref,
            day_key,
            todays_kcal,
            target_kcal,
            threshold_pct,
        )
        awarded = _award_streak_badges(client, user_id, _streak_current(streak))
    except StreakValidationError:
        raise
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to recalculate streak.",
            extra={"user_id": user_id, "day_key": day_key},
        )
        raise FirestoreServiceError("Failed to recalculate streak.") from exc

    return streak, awarded


async def sync_streak_from_meals(
    user_id: str,
    *,
    reference_day_key: str | None = None,
    threshold_pct: float = 0.8,
) -> tuple[StreakState, list[str]]:
    normalized_reference_day_key = reference_day_key or _utc_day_key()
    _validate_day_key(normalized_reference_day_key)

    client: firestore.Client = get_firestore()
    user_ref = client.collection(USERS_COLLECTION).document(user_id)
    streak_ref = _streak_ref(client, user_id)
    meals_ref = _meals_collection(client, user_id)

    try:
        user_snapshot = user_ref.get()
        user_data = dict(user_snapshot.to_dict() or {}) if user_snapshot.exists else {}
        target_kcal = parse_target_kcal(user_data)

        meal_snapshots = list(meals_ref.where(filter=FieldFilter("deleted", "==", False)).stream())
        daily_kcal: dict[str, float] = {}
        for snapshot in meal_snapshots:
            raw_meal = dict(snapshot.to_dict() or {})
            day_key = _extract_meal_day_key(raw_meal)
            if not day_key:
                continue
            daily_kcal[day_key] = daily_kcal.get(day_key, 0.0) + _extract_meal_kcal(raw_meal)

        streak = _build_streak_state_from_meals(
            daily_kcal=daily_kcal,
            target_kcal=target_kcal,
            threshold_pct=threshold_pct,
            reference_day_key=normalized_reference_day_key,
        )
        streak_ref.set(streak, merge=True)
        awarded = _award_streak_badges(client, user_id, _streak_current(streak))
    except StreakValidationError:
        raise
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to sync streak from meals.",
            extra={"user_id": user_id, "reference_day_key": normalized_reference_day_key},
        )
        raise FirestoreServiceError("Failed to sync streak from meals.") from exc

    return streak, awarded
