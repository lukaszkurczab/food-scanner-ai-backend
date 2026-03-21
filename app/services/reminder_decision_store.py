"""Idempotent daily send-opportunity tracker for Smart Reminders v1 frequency cap.

This is NOT a delivery state machine.  It tracks how many **unique** send
opportunities the backend decision layer has returned for a given user+day so
that the rule engine can enforce a hard daily cap.

A "send opportunity" is uniquely identified by ``(dayKey, kind, scheduledAtUtc)``.
Repeated evaluations that produce the same identity do NOT inflate the counter.

Storage: ``users/{userId}/reminderDailyStats/{dayKey}``  →
    ``{sendCount: int, emittedDecisionKeys: [str, ...]}``

Observability
-------------
All Firestore interactions emit structured logs with:
- ``operation``: ``read_count`` | ``write_decision``
- ``store_mode``: ``normal`` | ``degraded``
- ``user_id``, ``day_key``

The ``degraded`` flag on :class:`DailySendCountResult` lets callers know
internally that the count is a fail-open fallback, **without** changing
the public ``ReminderDecision`` contract.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from google.api_core.exceptions import GoogleAPICallError
from google.cloud import firestore

from app.core.exceptions import FirestoreServiceError
from app.db.firebase import get_firestore

logger = logging.getLogger(__name__)

USERS_COLLECTION = "users"
DAILY_STATS_SUBCOLLECTION = "reminderDailyStats"


@dataclass(frozen=True)
class DailySendCountResult:
    """Result of reading the daily send counter.

    Attributes:
        count:    Number of unique send opportunities recorded today.
        degraded: ``True`` when the count is a fail-open fallback (0)
                  because Firestore was unreachable.  ``False`` for a
                  genuine read (including a missing-document zero).
    """

    count: int
    degraded: bool


def _daily_stats_document(user_id: str, day_key: str) -> firestore.DocumentReference:
    client: firestore.Client = get_firestore()
    return (
        client.collection(USERS_COLLECTION)
        .document(user_id)
        .collection(DAILY_STATS_SUBCOLLECTION)
        .document(day_key)
    )


def build_decision_key(day_key: str, kind: str, scheduled_at_utc: str) -> str:
    """Build a stable identity key for a unique send opportunity.

    Format: ``{dayKey}:{kind}:{scheduledAtUtc}``
    """
    return f"{day_key}:{kind}:{scheduled_at_utc}"


async def get_daily_send_count(user_id: str, day_key: str) -> DailySendCountResult:
    """Return how many **unique** ``send`` opportunities the backend has issued today.

    Returns ``DailySendCountResult(count=0, degraded=True)`` on read failure
    (fail-open: cap never blocks if Firestore is down — the user just gets an
    un-capped reminder, which is safer than a false suppress).
    """
    try:
        doc = _daily_stats_document(user_id, day_key).get()
        if not doc.exists:
            logger.debug(
                "reminder.store.read_count",
                extra={
                    "user_id": user_id,
                    "day_key": day_key,
                    "operation": "read_count",
                    "store_mode": "normal",
                    "count": 0,
                },
            )
            return DailySendCountResult(count=0, degraded=False)

        count = int(doc.to_dict().get("sendCount", 0))
        logger.debug(
            "reminder.store.read_count",
            extra={
                "user_id": user_id,
                "day_key": day_key,
                "operation": "read_count",
                "store_mode": "normal",
                "count": count,
            },
        )
        return DailySendCountResult(count=count, degraded=False)

    except (GoogleAPICallError, FirestoreServiceError, Exception):
        logger.warning(
            "reminder.store.read_count.failed",
            extra={
                "user_id": user_id,
                "day_key": day_key,
                "operation": "read_count",
                "store_mode": "degraded",
                "fallback_count": 0,
            },
            exc_info=True,
        )
        return DailySendCountResult(count=0, degraded=True)


async def record_send_decision_if_new(
    user_id: str,
    day_key: str,
    kind: str,
    scheduled_at_utc: str,
) -> bool:
    """Record a send opportunity only if it has not been recorded before.

    Returns ``True`` if this was a **new** opportunity (counter incremented),
    ``False`` if it was already known (idempotent no-op).

    Uses a Firestore transaction to ensure atomicity: the read that checks
    for an existing ``decision_key`` and the conditional write happen in a
    single atomic unit, eliminating the race condition where two concurrent
    requests could both pass the duplicate check and both increment the
    counter.

    Best-effort: a write/transaction failure does NOT block the decision
    response.
    """
    decision_key = build_decision_key(day_key, kind, scheduled_at_utc)

    try:
        client: firestore.Client = get_firestore()
        doc_ref = _daily_stats_document(user_id, day_key)
        is_new: bool = False

        @firestore.transactional
        def _record_in_transaction(transaction: firestore.Transaction) -> bool:
            doc = doc_ref.get(transaction=transaction)

            if doc.exists:
                data = doc.to_dict() or {}
                emitted_keys = data.get("emittedDecisionKeys", [])
                if decision_key in emitted_keys:
                    return False
                new_count = int(data.get("sendCount", 0)) + 1
            else:
                emitted_keys = []
                new_count = 1

            transaction.set(
                doc_ref,
                {
                    "sendCount": new_count,
                    "emittedDecisionKeys": emitted_keys + [decision_key],
                },
                merge=True,
            )
            return True

        is_new = _record_in_transaction(client.transaction())

        if is_new:
            logger.debug(
                "reminder.store.write_decision.recorded",
                extra={
                    "user_id": user_id,
                    "day_key": day_key,
                    "operation": "write_decision",
                    "store_mode": "normal",
                    "decision_key": decision_key,
                    "duplicate": False,
                },
            )
        else:
            logger.debug(
                "reminder.store.write_decision.duplicate",
                extra={
                    "user_id": user_id,
                    "day_key": day_key,
                    "operation": "write_decision",
                    "store_mode": "normal",
                    "decision_key": decision_key,
                    "duplicate": True,
                },
            )

        return is_new

    except (GoogleAPICallError, Exception):
        logger.warning(
            "reminder.store.write_decision.failed",
            extra={
                "user_id": user_id,
                "day_key": day_key,
                "operation": "write_decision",
                "store_mode": "degraded",
                "decision_key": decision_key,
            },
            exc_info=True,
        )
        return False
