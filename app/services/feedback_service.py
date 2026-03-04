"""Backend-owned feedback submission flow."""

from datetime import UTC, datetime
import logging
from typing import Any
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

logger = logging.getLogger(__name__)

USERS_COLLECTION = "users"
FEEDBACK_SUBCOLLECTION = "feedback"


class FeedbackValidationError(Exception):
    """Raised when feedback payload is invalid."""


def _utc_timestamp_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def _feedback_collection(user_id: str) -> firestore.CollectionReference:
    client: firestore.Client = get_firestore()
    return client.collection(USERS_COLLECTION).document(user_id).collection(FEEDBACK_SUBCOLLECTION)


def _normalize_message(message: str) -> str:
    normalized = str(message or "").strip()
    if not normalized:
        raise FeedbackValidationError("Feedback message is required.")
    if len(normalized) > 500:
        raise FeedbackValidationError("Feedback message is too long.")
    return normalized


def _normalize_device_info(payload: dict[str, Any] | None) -> dict[str, str | None] | None:
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise FeedbackValidationError("Invalid device info.")

    model_name = payload.get("modelName")
    os_name = payload.get("osName")
    os_version = payload.get("osVersion")

    return {
        "modelName": model_name if isinstance(model_name, str) and model_name.strip() else None,
        "osName": os_name if isinstance(os_name, str) and os_name.strip() else None,
        "osVersion": os_version if isinstance(os_version, str) and os_version.strip() else None,
    }


async def _upload_attachment(
    *,
    user_id: str,
    feedback_id: str,
    upload: UploadFile,
) -> tuple[str, str]:
    bucket = get_storage_bucket()
    extension = "jpg"
    if upload.filename and "." in upload.filename:
        maybe_extension = upload.filename.rsplit(".", 1)[-1].strip().lower()
        if maybe_extension:
            extension = maybe_extension

    filename = upload.filename.rsplit("/", 1)[-1] if upload.filename else f"attachment.{extension}"
    object_path = f"feedback/{user_id}/{feedback_id}/{filename}"
    token = str(uuid4())
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
            "Failed to upload feedback attachment.",
            extra={"user_id": user_id, "feedback_id": feedback_id},
        )
        raise FirestoreServiceError("Failed to upload feedback attachment.") from exc
    finally:
        upload.file.close()

    return (
        build_storage_download_url(
            get_storage_bucket_name(bucket),
            object_path,
            token,
        ),
        object_path,
    )


async def create_feedback(
    *,
    user_id: str,
    message: str,
    email: str | None = None,
    device_info: dict[str, Any] | None = None,
    attachment: UploadFile | None = None,
) -> dict[str, Any]:
    normalized_message = _normalize_message(message)
    normalized_email = str(email or "").strip() or None
    normalized_device_info = _normalize_device_info(device_info)
    created_at = _utc_timestamp_ms()

    collection_ref = _feedback_collection(user_id)
    document_ref = collection_ref.document()
    payload: dict[str, Any] = {
        "id": document_ref.id,
        "message": normalized_message,
        "userUid": user_id,
        "email": normalized_email,
        "deviceInfo": normalized_device_info,
        "createdAt": created_at,
        "updatedAt": None,
        "status": "new",
        "attachmentUrl": None,
        "attachmentPath": None,
    }

    try:
        if attachment is not None:
            attachment_url, attachment_path = await _upload_attachment(
                user_id=user_id,
                feedback_id=document_ref.id,
                upload=attachment,
            )
            payload["attachmentUrl"] = attachment_url
            payload["attachmentPath"] = attachment_path
            payload["updatedAt"] = _utc_timestamp_ms()

        document_ref.set(payload, merge=True)
    except FeedbackValidationError:
        raise
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to create feedback.",
            extra={"user_id": user_id, "feedback_id": document_ref.id},
        )
        raise FirestoreServiceError("Failed to create feedback.") from exc

    return payload
