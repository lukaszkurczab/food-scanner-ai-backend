"""Service helpers for tracking per-user daily AI usage in Firestore.

The service stores one document per user and UTC day in the ``ai_usage``
collection. Reads expose the current usage state, while writes use a Firestore
transaction so concurrent requests cannot overrun the configured daily limit.
"""

from datetime import datetime, timezone
import logging

from firebase_admin.exceptions import FirebaseError
from google.api_core.exceptions import GoogleAPICallError, RetryError
from google.cloud import firestore

from app.core.config import settings
from app.core.exceptions import AiUsageLimitExceededError, FirestoreServiceError
from app.db.firebase import get_firestore

logger = logging.getLogger(__name__)

COLLECTION_NAME = "ai_usage"


def get_date_key() -> str:
    """Return today's UTC date key in ``YYYY-MM-DD`` format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


async def get_usage(user_id: str) -> tuple[int, int, str]:
    """Return current usage count, daily limit, and date key for a user."""
    client: firestore.Client = get_firestore()
    date_key = get_date_key()
    daily_limit = settings.AI_DAILY_LIMIT_FREE
    document_id = f"{user_id}-{date_key}"

    try:
        snapshot = client.collection(COLLECTION_NAME).document(document_id).get()
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to fetch AI usage document.",
            extra={"user_id": user_id, "date_key": date_key},
        )
        raise FirestoreServiceError("Failed to fetch AI usage document.") from exc

    if not snapshot.exists:
        return 0, daily_limit, date_key

    data = snapshot.to_dict() or {}
    usage_count = int(data.get("usageCount", 0))
    return usage_count, daily_limit, str(data.get("dateKey") or date_key)


@firestore.transactional
def _increment_usage_transaction(
    transaction: firestore.Transaction,
    document_ref: firestore.DocumentReference,
    date_key: str,
    daily_limit: int,
) -> int:
    snapshot = document_ref.get(transaction=transaction)
    data = snapshot.to_dict() if snapshot.exists else {}
    stored_date_key = data.get("dateKey")

    if not snapshot.exists or stored_date_key != date_key:
        usage_count = 1
    else:
        usage_count = int(data.get("usageCount", 0)) + 1

    if usage_count > daily_limit:
        raise AiUsageLimitExceededError("AI usage limit exceeded.")

    transaction.set(
        document_ref,
        {
            "usageCount": usage_count,
            "dateKey": date_key,
            "updatedAt": datetime.now(timezone.utc),
        },
    )
    return usage_count


async def increment_usage(user_id: str) -> tuple[int, int, str]:
    """Atomically increment daily AI usage for a user."""
    client: firestore.Client = get_firestore()
    date_key = get_date_key()
    daily_limit = settings.AI_DAILY_LIMIT_FREE
    document_id = f"{user_id}-{date_key}"
    document_ref = client.collection(COLLECTION_NAME).document(document_id)
    transaction = client.transaction()

    try:
        usage_count = _increment_usage_transaction(
            transaction,
            document_ref,
            date_key,
            daily_limit,
        )
    except AiUsageLimitExceededError:
        raise
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to update AI usage document.",
            extra={"user_id": user_id, "date_key": date_key},
        )
        raise FirestoreServiceError("Failed to update AI usage document.") from exc

    return usage_count, daily_limit, date_key
