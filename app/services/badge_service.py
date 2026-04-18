"""Backend-owned badge mutation flows."""

from datetime import datetime, timezone
import logging
from typing import Any, cast

from firebase_admin.exceptions import FirebaseError
from google.api_core.exceptions import GoogleAPICallError, RetryError
from google.cloud import firestore

from app.core.exceptions import FirestoreServiceError
from app.core.firestore_constants import BADGES_SUBCOLLECTION, USERS_COLLECTION
from app.db.firebase import get_firestore

logger = logging.getLogger(__name__)
DAY_MS = 86_400_000
PREMIUM_BADGE_SPECS: tuple[dict[str, object], ...] = (
    {
        "id": "premium_start",
        "label": "Premium started",
        "milestone": "start",
        "icon": "⭐",
        "color": "#F7A541",
    },
    {
        "id": "premium_90d",
        "label": "Premium 3m",
        "milestone": 90,
        "icon": "⭐",
        "color": "#F28B30",
    },
    {
        "id": "premium_365d",
        "label": "Premium 12m",
        "milestone": 365,
        "icon": "👑",
        "color": "#C9A227",
    },
    {
        "id": "premium_730d",
        "label": "Premium 24m",
        "milestone": 730,
        "icon": "💎",
        "color": "#C2E6F9",
    },
)


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _badge_collection(
    client: firestore.Client, user_id: str
) -> firestore.CollectionReference:
    return client.collection(USERS_COLLECTION).document(user_id).collection(BADGES_SUBCOLLECTION)


def _badge_payload(spec: dict[str, object], unlocked_at: int) -> dict[str, object]:
    return {
        "id": spec["id"],
        "type": "premium",
        "label": spec["label"],
        "milestone": spec["milestone"],
        "icon": spec["icon"],
        "color": spec["color"],
        "unlockedAt": unlocked_at,
    }


def _read_unlocked_at(raw: object, fallback: int) -> int:
    if not isinstance(raw, dict):
        return fallback
    raw_map = cast(dict[object, object], raw)
    unlocked_at = raw_map.get("unlockedAt")
    return unlocked_at if isinstance(unlocked_at, int) and unlocked_at >= 0 else fallback


def _normalize_badge_snapshot(snapshot: firestore.DocumentSnapshot) -> dict[str, Any] | None:
    raw = snapshot.to_dict() or {}
    if not isinstance(raw, dict):
        return None

    badge_id = raw.get("id")
    badge_type = raw.get("type")
    label = raw.get("label")
    milestone = raw.get("milestone")
    icon = raw.get("icon")
    color = raw.get("color")
    unlocked_at = raw.get("unlockedAt")

    if not isinstance(badge_id, str) or not badge_id:
        return None
    if not isinstance(badge_type, str) or not badge_type:
        return None
    if not isinstance(label, str) or not label:
        return None
    if not isinstance(milestone, (int, str)):
        return None
    if not isinstance(icon, str) or not icon:
        return None
    if not isinstance(color, str) or not color:
        return None
    if not isinstance(unlocked_at, int) or unlocked_at < 0:
        return None

    return {
        "id": badge_id,
        "type": badge_type,
        "label": label,
        "milestone": milestone,
        "icon": icon,
        "color": color,
        "unlockedAt": unlocked_at,
    }


@firestore.transactional
def _reconcile_premium_badges_transaction(
    transaction: firestore.Transaction,
    badges_collection: Any,
    is_premium: bool,
    now_ms: int,
) -> tuple[list[str], bool]:
    badge_refs = {
        str(spec["id"]): badges_collection.document(str(spec["id"]))
        for spec in PREMIUM_BADGE_SPECS
    }
    badge_snapshots = {
        badge_id: badge_ref.get(transaction=transaction)
        for badge_id, badge_ref in badge_refs.items()
    }

    has_premium_badge = any(snapshot.exists for snapshot in badge_snapshots.values())
    if not is_premium:
        return [], has_premium_badge

    awarded_badges: list[str] = []
    start_spec = PREMIUM_BADGE_SPECS[0]
    start_id = str(start_spec["id"])
    start_snapshot = badge_snapshots[start_id]
    start_unlocked_at = now_ms

    if not start_snapshot.exists:
        transaction.set(
            badge_refs[start_id],
            _badge_payload(start_spec, now_ms),
            merge=True,
        )
        awarded_badges.append(start_id)
    else:
        start_unlocked_at = _read_unlocked_at(start_snapshot.to_dict() or {}, now_ms)

    days_since_start = max(0, (now_ms - start_unlocked_at) // DAY_MS)

    for spec in PREMIUM_BADGE_SPECS[1:]:
        badge_id = str(spec["id"])
        milestone = spec["milestone"]
        if not isinstance(milestone, int) or days_since_start < milestone:
            continue
        if badge_snapshots[badge_id].exists:
            continue
        transaction.set(
            badge_refs[badge_id],
            _badge_payload(spec, now_ms),
            merge=True,
        )
        awarded_badges.append(badge_id)

    return awarded_badges, True


async def reconcile_premium_badges(
    user_id: str,
    *,
    is_premium: bool,
    now_ms: int | None = None,
) -> tuple[list[str], bool]:
    client: firestore.Client = get_firestore()
    transaction = client.transaction()
    badges_collection = _badge_collection(client, user_id)

    try:
        return _reconcile_premium_badges_transaction(
            transaction,
            badges_collection,
            is_premium,
            now_ms if isinstance(now_ms, int) and now_ms >= 0 else _now_ms(),
        )
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to reconcile premium badges.",
            extra={"user_id": user_id, "is_premium": is_premium},
        )
        raise FirestoreServiceError("Failed to reconcile premium badges.") from exc


async def list_badges(user_id: str) -> list[dict[str, Any]]:
    client: firestore.Client = get_firestore()
    badges_collection = _badge_collection(client, user_id)

    try:
        snapshots = list(
            badges_collection.order_by(
                "unlockedAt",
                direction=firestore.Query.ASCENDING,
            ).stream()
        )
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception("Failed to list badges.", extra={"user_id": user_id})
        raise FirestoreServiceError("Failed to list badges.") from exc

    items = [
        normalized
        for snapshot in snapshots
        if (normalized := _normalize_badge_snapshot(snapshot)) is not None
    ]
    items.sort(key=lambda item: (item["unlockedAt"], item["id"]))
    return items
