"""Business logic for account/profile mutations owned by the backend."""

from datetime import datetime, timezone
import logging
import re
from typing import Any, cast
from uuid import uuid4

from fastapi import UploadFile
from firebase_admin.exceptions import FirebaseError
from google.api_core.exceptions import GoogleAPICallError, RetryError
from google.cloud import firestore

from app.core.exceptions import FirestoreServiceError
from app.db.firebase import (
    build_storage_download_url,
    get_firestore,
    get_storage_bucket,
    get_storage_bucket_name,
)
from app.services import streak_service
from app.services.username_service import normalize_username

logger = logging.getLogger(__name__)

USERS_COLLECTION = "users"
USERNAMES_COLLECTION = "usernames"
DELETE_SUBCOLLECTIONS = (
    "meals",
    "myMeals",
    "chat_messages",
    "notifications",
    "prefs",
    "notif_meta",
    "feedback",
)
BATCH_DELETE_LIMIT = 500
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
CHAT_THREADS_SUBCOLLECTION = "chat_threads"
CHAT_MESSAGES_SUBCOLLECTION = "messages"
FEEDBACK_SUBCOLLECTION = "feedback"
MY_MEALS_SUBCOLLECTION = "myMeals"
EDITABLE_PROFILE_FIELDS = frozenset(
    {
        "unitsSystem",
        "age",
        "sex",
        "height",
        "heightInch",
        "weight",
        "preferences",
        "activityLevel",
        "goal",
        "calorieDeficit",
        "calorieSurplus",
        "chronicDiseases",
        "chronicDiseasesOther",
        "allergies",
        "allergiesOther",
        "lifestyle",
        "aiStyle",
        "aiFocus",
        "aiFocusOther",
        "aiNote",
        "surveyComplited",
        "surveyCompletedAt",
        "calorieTarget",
        "darkTheme",
        "language",
    }
)


class EmailValidationError(Exception):
    """Raised when the email pending payload is invalid."""


class AvatarMetadataValidationError(Exception):
    """Raised when avatar metadata payload is invalid."""


class UserProfileValidationError(Exception):
    """Raised when the user profile payload contains forbidden fields."""


def normalize_email(raw: object) -> str:
    return str(raw or "").strip()


def _validate_email(email: str) -> None:
    if not EMAIL_RE.match(email):
        raise EmailValidationError("Invalid email address.")


def _validate_avatar_url(avatar_url: str) -> None:
    if not avatar_url.startswith(("http://", "https://")):
        raise AvatarMetadataValidationError("Invalid avatar URL.")


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _utc_timestamp_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _sanitize_profile_patch(payload: dict[str, Any]) -> dict[str, Any]:
    invalid_keys = sorted(key for key in payload if key not in EDITABLE_PROFILE_FIELDS)
    if invalid_keys:
        joined = ", ".join(invalid_keys)
        raise UserProfileValidationError(f"Forbidden profile fields: {joined}")

    return dict(payload)


async def set_email_pending(user_id: str, email: str) -> str:
    normalized_email = normalize_email(email)
    _validate_email(normalized_email)

    client: firestore.Client = get_firestore()
    user_ref = client.collection(USERS_COLLECTION).document(user_id)

    try:
        user_ref.set({"emailPending": normalized_email}, merge=True)
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to persist email pending state.",
            extra={"user_id": user_id},
        )
        raise FirestoreServiceError("Failed to persist email pending state.") from exc

    return normalized_email


async def set_avatar_metadata(user_id: str, avatar_url: str) -> tuple[str, str]:
    normalized_avatar_url = str(avatar_url or "").strip()
    _validate_avatar_url(normalized_avatar_url)
    synced_at = _utc_timestamp()

    client: firestore.Client = get_firestore()
    user_ref = client.collection(USERS_COLLECTION).document(user_id)

    try:
        user_ref.set(
            {
                "avatarUrl": normalized_avatar_url,
                "avatarlastSyncedAt": synced_at,
                "avatarLocalPath": firestore.DELETE_FIELD,
            },
            merge=True,
        )
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to persist avatar metadata.",
            extra={"user_id": user_id},
        )
        raise FirestoreServiceError("Failed to persist avatar metadata.") from exc

    return normalized_avatar_url, synced_at


async def upload_avatar(user_id: str, upload: UploadFile) -> tuple[str, str]:
    bucket = get_storage_bucket()
    token = str(uuid4())
    object_path = f"avatars/{user_id}/avatar.jpg"
    blob = bucket.blob(object_path)

    try:
        upload.file.seek(0)
        blob.metadata = {"firebaseStorageDownloadTokens": token}
        blob.upload_from_file(
            upload.file,
            content_type=upload.content_type or "image/jpeg",
        )
        blob.patch()
    except (FirebaseError, GoogleAPICallError, RetryError, OSError) as exc:
        logger.exception(
            "Failed to upload avatar.",
            extra={"user_id": user_id},
        )
        raise FirestoreServiceError("Failed to upload avatar.") from exc
    finally:
        upload.file.close()

    avatar_url = build_storage_download_url(
        get_storage_bucket_name(bucket),
        object_path,
        token,
    )
    return await set_avatar_metadata(user_id, avatar_url)


async def get_user_profile_data(user_id: str) -> dict[str, Any] | None:
    client: firestore.Client = get_firestore()
    user_ref = client.collection(USERS_COLLECTION).document(user_id)

    try:
        snapshot = user_ref.get()
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to fetch user profile data.",
            extra={"user_id": user_id},
        )
        raise FirestoreServiceError("Failed to fetch user profile data.") from exc

    if not snapshot.exists:
        return None

    return dict(snapshot.to_dict() or {})


async def upsert_user_profile_data(
    user_id: str,
    payload: dict[str, Any],
    *,
    auth_email: str | None = None,
) -> dict[str, Any]:
    sanitized_patch = _sanitize_profile_patch(payload)
    client: firestore.Client = get_firestore()
    user_ref = client.collection(USERS_COLLECTION).document(user_id)

    try:
        snapshot = user_ref.get()
        existing = dict(snapshot.to_dict() or {}) if snapshot.exists else {}

        document: dict[str, Any] = {"uid": user_id}
        normalized_email = normalize_email(auth_email)
        if normalized_email:
            document["email"] = normalized_email
        if "createdAt" not in existing:
            document["createdAt"] = _utc_timestamp_ms()
        if "plan" not in existing:
            document["plan"] = "free"
        if "syncState" not in existing:
            document["syncState"] = "pending"
        if "lastLogin" not in existing:
            document["lastLogin"] = _utc_timestamp()

        document.update(sanitized_patch)
        user_ref.set(document, merge=True)
    except UserProfileValidationError:
        raise
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to upsert user profile data.",
            extra={"user_id": user_id},
        )
        raise FirestoreServiceError("Failed to upsert user profile data.") from exc

    merged = dict(existing)
    merged.update(document)

    if "calorieTarget" in sanitized_patch:
        await streak_service.sync_streak_from_meals(user_id)

    return merged


def _delete_documents_in_batches(
    client: firestore.Client,
    documents: list[firestore.DocumentSnapshot],
) -> None:
    for index in range(0, len(documents), BATCH_DELETE_LIMIT):
        batch = client.batch()
        for document in documents[index : index + BATCH_DELETE_LIMIT]:
            batch.delete(document.reference)
        batch.commit()


def _read_subcollection_documents(
    user_ref: firestore.DocumentReference,
    subcollection_name: str,
) -> list[dict[str, Any]]:
    return [
        dict(document.to_dict() or {})
        for document in user_ref.collection(subcollection_name).stream()
    ]


def _delete_chat_threads(
    client: firestore.Client,
    user_ref: firestore.DocumentReference,
) -> None:
    thread_documents = list(user_ref.collection(CHAT_THREADS_SUBCOLLECTION).stream())
    for thread_document in thread_documents:
        message_documents = list(
            thread_document.reference.collection(CHAT_MESSAGES_SUBCOLLECTION).stream()
        )
        if message_documents:
            _delete_documents_in_batches(client, message_documents)

    if thread_documents:
        _delete_documents_in_batches(client, thread_documents)


def _read_chat_thread_messages(
    user_ref: firestore.DocumentReference,
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for thread_document in user_ref.collection(CHAT_THREADS_SUBCOLLECTION).stream():
        thread_id = thread_document.id
        thread_data = dict(thread_document.to_dict() or {})
        for message_document in thread_document.reference.collection(
            CHAT_MESSAGES_SUBCOLLECTION
        ).stream():
            payload = dict(message_document.to_dict() or {})
            payload.setdefault("id", message_document.id)
            payload.setdefault("threadId", thread_id)
            if thread_data.get("title") and "threadTitle" not in payload:
                payload["threadTitle"] = thread_data["title"]
            messages.append(payload)
    return messages


def _delete_feedback_attachments(feedback_documents: list[firestore.DocumentSnapshot]) -> None:
    if not feedback_documents:
        return

    bucket = get_storage_bucket()
    for document in feedback_documents:
        payload = dict(document.to_dict() or {})
        attachment_path = payload.get("attachmentPath")
        if not isinstance(attachment_path, str) or not attachment_path.strip():
            continue
        try:
            bucket.blob(attachment_path).delete()
        except Exception:
            logger.exception(
                "Failed to delete feedback attachment.",
                extra={"feedback_id": document.id},
            )


def _delete_storage_prefix(bucket: Any, prefix: str) -> None:
    for blob in bucket.list_blobs(prefix=prefix):
        blob.delete()


def _delete_user_storage_assets(user_id: str) -> None:
    bucket = get_storage_bucket()
    prefixes = (
        f"avatars/{user_id}/",
        f"meals/{user_id}/",
        f"myMeals/{user_id}/",
    )
    for prefix in prefixes:
        _delete_storage_prefix(bucket, prefix)


async def delete_account_data(user_id: str) -> None:
    client: firestore.Client = get_firestore()
    user_ref = client.collection(USERS_COLLECTION).document(user_id)

    try:
        user_snapshot = user_ref.get()
        username = ""
        if user_snapshot.exists:
            user_data: dict[str, object] = user_snapshot.to_dict() or {}
            username = normalize_username(user_data.get("username"))

        feedback_documents = list(user_ref.collection(FEEDBACK_SUBCOLLECTION).stream())
        _delete_feedback_attachments(feedback_documents)
        _delete_user_storage_assets(user_id)

        for subcollection_name in DELETE_SUBCOLLECTIONS:
            documents = (
                feedback_documents
                if subcollection_name == FEEDBACK_SUBCOLLECTION
                else list(user_ref.collection(subcollection_name).stream())
            )
            if documents:
                _delete_documents_in_batches(client, documents)

        _delete_chat_threads(client, user_ref)

        if username:
            client.collection(USERNAMES_COLLECTION).document(username).delete()

        user_ref.delete()
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to delete account data.",
            extra={"user_id": user_id},
        )
        raise FirestoreServiceError("Failed to delete account data.") from exc


async def get_user_export_data(
    user_id: str,
) -> tuple[
    dict[str, Any] | None,
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any],
    list[dict[str, Any]],
]:
    client: firestore.Client = get_firestore()
    user_ref = client.collection(USERS_COLLECTION).document(user_id)

    try:
        user_snapshot = user_ref.get()
        profile = dict(user_snapshot.to_dict() or {}) if user_snapshot.exists else None
        meals = _read_subcollection_documents(user_ref, "meals")
        my_meals = _read_subcollection_documents(user_ref, MY_MEALS_SUBCOLLECTION)
        chat_messages = _read_chat_thread_messages(user_ref)
        notifications = _read_subcollection_documents(user_ref, "notifications")
        prefs_documents = _read_subcollection_documents(user_ref, "prefs")
        feedback = _read_subcollection_documents(user_ref, FEEDBACK_SUBCOLLECTION)
        notification_prefs = {}
        for document in prefs_documents:
            notifications_value = document.get("notifications")
            if isinstance(notifications_value, dict):
                notification_prefs = dict(cast(dict[str, Any], notifications_value))
                break
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to build user export payload.",
            extra={"user_id": user_id},
        )
        raise FirestoreServiceError("Failed to build user export payload.") from exc

    return profile, meals, my_meals, chat_messages, notifications, notification_prefs, feedback
