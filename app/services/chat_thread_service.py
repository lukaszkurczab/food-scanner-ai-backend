"""Backend-owned storage for chat threads and messages."""

import logging
from typing import Any

from firebase_admin.exceptions import FirebaseError
from google.api_core.exceptions import GoogleAPICallError, RetryError
from google.cloud import firestore

from app.core.exceptions import FirestoreServiceError
from app.db.firebase import get_firestore

logger = logging.getLogger(__name__)

USERS_COLLECTION = "users"
CHAT_THREADS_SUBCOLLECTION = "chat_threads"
MESSAGES_SUBCOLLECTION = "messages"


def _threads_collection(user_id: str) -> firestore.CollectionReference:
    client: firestore.Client = get_firestore()
    return (
        client.collection(USERS_COLLECTION)
        .document(user_id)
        .collection(CHAT_THREADS_SUBCOLLECTION)
    )


def _thread_ref(user_id: str, thread_id: str) -> firestore.DocumentReference:
    return _threads_collection(user_id).document(thread_id)


def _messages_collection(
    user_id: str,
    thread_id: str,
) -> firestore.CollectionReference:
    return _thread_ref(user_id, thread_id).collection(MESSAGES_SUBCOLLECTION)


def _normalize_thread(
    snapshot: firestore.DocumentSnapshot,
) -> dict[str, Any]:
    data = dict(snapshot.to_dict() or {})
    return {
        "id": snapshot.id,
        "title": str(data.get("title") or ""),
        "createdAt": int(data.get("createdAt") or 0),
        "updatedAt": int(data.get("updatedAt") or 0),
        "lastMessage": str(data.get("lastMessage") or "") or None,
        "lastMessageAt": (
            int(data.get("lastMessageAt"))
            if data.get("lastMessageAt") is not None
            else None
        ),
    }


def _normalize_message(
    snapshot: firestore.DocumentSnapshot,
) -> dict[str, Any]:
    data = dict(snapshot.to_dict() or {})
    role = str(data.get("role") or "assistant")
    if role not in {"user", "assistant", "system"}:
        role = "assistant"

    created_at = int(data.get("createdAt") or 0)
    return {
        "id": snapshot.id,
        "role": role,
        "content": str(data.get("content") or ""),
        "createdAt": created_at,
        "lastSyncedAt": int(data.get("lastSyncedAt") or created_at),
        "deleted": bool(data.get("deleted") or False),
    }


async def list_threads(
    user_id: str,
    *,
    limit_count: int = 20,
    before_updated_at: int | None = None,
) -> tuple[list[dict[str, Any]], int | None]:
    threads_ref = _threads_collection(user_id)

    try:
        query = threads_ref.order_by("updatedAt", direction=firestore.Query.DESCENDING)
        if before_updated_at is not None:
            query = query.where("updatedAt", "<", before_updated_at)
        snapshots = list(query.limit(limit_count).stream())
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to list chat threads.",
            extra={"user_id": user_id},
        )
        raise FirestoreServiceError("Failed to list chat threads.") from exc

    items = [_normalize_thread(snapshot) for snapshot in snapshots]
    next_before_updated_at = items[-1]["updatedAt"] if len(items) == limit_count else None
    return items, next_before_updated_at


async def list_messages(
    user_id: str,
    thread_id: str,
    *,
    limit_count: int = 50,
    before_created_at: int | None = None,
) -> tuple[list[dict[str, Any]], int | None]:
    messages_ref = _messages_collection(user_id, thread_id)

    try:
        query = messages_ref.order_by("createdAt", direction=firestore.Query.DESCENDING)
        if before_created_at is not None:
            query = query.where("createdAt", "<", before_created_at)
        snapshots = list(query.limit(limit_count).stream())
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to list chat messages.",
            extra={"user_id": user_id, "thread_id": thread_id},
        )
        raise FirestoreServiceError("Failed to list chat messages.") from exc

    items = [_normalize_message(snapshot) for snapshot in snapshots]
    next_before_created_at = items[-1]["createdAt"] if len(items) == limit_count else None
    return items, next_before_created_at


async def persist_message(
    user_id: str,
    thread_id: str,
    *,
    message_id: str,
    role: str,
    content: str,
    created_at: int,
    title: str | None = None,
) -> None:
    thread_ref = _thread_ref(user_id, thread_id)
    message_ref = _messages_collection(user_id, thread_id).document(message_id)
    client: firestore.Client = get_firestore()

    try:
        thread_snapshot = thread_ref.get()
        batch = client.batch()
        batch.set(
            message_ref,
            {
                "role": role,
                "content": content,
                "createdAt": created_at,
                "lastSyncedAt": created_at,
                "deleted": False,
            },
            merge=True,
        )

        thread_payload: dict[str, Any] = {
            "updatedAt": created_at,
            "lastMessage": content,
            "lastMessageAt": created_at,
        }
        if not thread_snapshot.exists:
            thread_payload["createdAt"] = created_at
        if role == "user" and title:
            thread_payload["title"] = title
        batch.set(thread_ref, thread_payload, merge=True)
        batch.commit()
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to persist chat message.",
            extra={"user_id": user_id, "thread_id": thread_id, "message_id": message_id},
        )
        raise FirestoreServiceError("Failed to persist chat message.") from exc
