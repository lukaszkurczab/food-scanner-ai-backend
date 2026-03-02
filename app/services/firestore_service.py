"""Helpers for Firestore access used by service and API layers.

Routers should call these helpers instead of talking to Firestore directly so
database access stays centralized behind a small service boundary.
"""

from typing import Any
import logging

from firebase_admin.exceptions import FirebaseError
from google.api_core.exceptions import GoogleAPICallError, RetryError
from google.cloud import firestore

from app.core.exceptions import FirestoreServiceError
from app.db import firebase as firebase_db

logger = logging.getLogger(__name__)


async def get_document(collection: str, document_id: str) -> dict[str, Any] | None:
    """Fetch one Firestore document by ID through the service boundary.

    Routers and other callers should use this helper instead of calling the
    Firestore client directly.
    """
    client: firestore.Client = firebase_db.get_firestore()

    try:
        snapshot = client.collection(collection).document(document_id).get()
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to fetch Firestore document.",
            extra={"collection": collection, "document_id": document_id},
        )
        raise FirestoreServiceError("Failed to fetch Firestore document.") from exc

    if not snapshot.exists:
        return None

    return snapshot.to_dict()


async def set_document(collection: str, document_id: str, data: dict[str, Any]) -> None:
    """Create or replace one Firestore document through the service boundary.

    Routers and other callers should use this helper instead of calling the
    Firestore client directly.
    """
    client: firestore.Client = firebase_db.get_firestore()

    try:
        client.collection(collection).document(document_id).set(data)
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to set Firestore document.",
            extra={"collection": collection, "document_id": document_id},
        )
        raise FirestoreServiceError("Failed to set Firestore document.") from exc


async def update_document(collection: str, document_id: str, data: dict[str, Any]) -> None:
    """Update fields on one Firestore document through the service boundary.

    Routers and other callers should use this helper instead of calling the
    Firestore client directly.
    """
    client: firestore.Client = firebase_db.get_firestore()

    try:
        client.collection(collection).document(document_id).update(data)
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to update Firestore document.",
            extra={"collection": collection, "document_id": document_id},
        )
        raise FirestoreServiceError("Failed to update Firestore document.") from exc
