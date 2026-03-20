"""Lightweight daily send-decision counter for Smart Reminders v1 frequency cap.

This is NOT a delivery state machine.  It tracks how many times the backend
decision layer returned ``send`` for a given user+day so that the rule engine
can enforce a hard daily cap.

Storage: ``users/{userId}/reminderDailyStats/{dayKey}``  →  ``{sendCount: int}``
"""

from __future__ import annotations

import logging

from google.api_core.exceptions import GoogleAPICallError
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from app.core.exceptions import FirestoreServiceError
from app.db.firebase import get_firestore

logger = logging.getLogger(__name__)

USERS_COLLECTION = "users"
DAILY_STATS_SUBCOLLECTION = "reminderDailyStats"


def _daily_stats_document(user_id: str, day_key: str) -> firestore.DocumentReference:
    client: firestore.Client = get_firestore()
    return (
        client.collection(USERS_COLLECTION)
        .document(user_id)
        .collection(DAILY_STATS_SUBCOLLECTION)
        .document(day_key)
    )


async def get_daily_send_count(user_id: str, day_key: str) -> int:
    """Return how many ``send`` decisions the backend has issued today.

    Returns ``0`` on missing document or read failure (fail-open: cap never
    blocks if Firestore is down — the user just gets an un-capped reminder,
    which is safer than a false suppress).
    """
    try:
        doc = _daily_stats_document(user_id, day_key).get()
        if not doc.exists:
            return 0
        return int(doc.to_dict().get("sendCount", 0))
    except (GoogleAPICallError, FirestoreServiceError, Exception):
        logger.warning(
            "Failed to read daily send count, defaulting to 0.",
            extra={"user_id": user_id, "day_key": day_key},
            exc_info=True,
        )
        return 0


async def record_send_decision(user_id: str, day_key: str) -> None:
    """Increment today's send-decision counter after the engine returned ``send``.

    Best-effort: a write failure does NOT block the decision response.
    """
    try:
        doc_ref = _daily_stats_document(user_id, day_key)
        doc_ref.set(
            {"sendCount": firestore.Increment(1)},
            merge=True,
        )
    except (GoogleAPICallError, Exception):
        logger.warning(
            "Failed to record send decision count.",
            extra={"user_id": user_id, "day_key": day_key},
            exc_info=True,
        )
