"""Business logic for username availability and atomic claim/change flows."""

import logging

from firebase_admin.exceptions import FirebaseError
from google.api_core.exceptions import GoogleAPICallError, RetryError
from google.cloud import firestore

from app.core.exceptions import FirestoreServiceError
from app.db.firebase import get_firestore

logger = logging.getLogger(__name__)

USERS_COLLECTION = "users"
USERNAMES_COLLECTION = "usernames"
MIN_USERNAME_LENGTH = 3


class UsernameUnavailableError(Exception):
    """Raised when the requested username is already claimed by another user."""


class UsernameValidationError(Exception):
    """Raised when the requested username fails backend validation."""


def normalize_username(raw: object) -> str:
    return str(raw or "").strip().lower()


def _is_valid_username(username: str) -> bool:
    return len(username) >= MIN_USERNAME_LENGTH


async def is_username_available(
    username: str,
    current_user_id: str | None = None,
) -> tuple[str, bool]:
    normalized_username = normalize_username(username)
    if not _is_valid_username(normalized_username):
        return normalized_username, False

    client: firestore.Client = get_firestore()
    document_ref = client.collection(USERNAMES_COLLECTION).document(normalized_username)

    try:
        snapshot = document_ref.get()
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to fetch username availability.",
            extra={"username": normalized_username},
        )
        raise FirestoreServiceError("Failed to fetch username availability.") from exc

    if not snapshot.exists:
        return normalized_username, True

    data = snapshot.to_dict() or {}
    owner_id = data.get("uid")
    if isinstance(owner_id, str) and current_user_id and owner_id == current_user_id:
        return normalized_username, True

    return normalized_username, False


@firestore.transactional
def _claim_username_transaction(
    transaction: firestore.Transaction,
    user_ref: firestore.DocumentReference,
    username_ref: firestore.DocumentReference,
    usernames_collection: firestore.CollectionReference,
    user_id: str,
    normalized_username: str,
) -> None:
    username_snapshot = username_ref.get(transaction=transaction)
    if username_snapshot.exists:
        username_data: dict[str, object] = username_snapshot.to_dict() or {}
        owner_id = username_data.get("uid")
        if isinstance(owner_id, str) and owner_id and owner_id != user_id:
            raise UsernameUnavailableError("Username unavailable.")

    previous_username = ""
    user_snapshot = user_ref.get(transaction=transaction)
    if user_snapshot.exists:
        user_data: dict[str, object] = user_snapshot.to_dict() or {}
        previous_username = normalize_username(user_data.get("username"))

    transaction.set(username_ref, {"uid": user_id}, merge=True)
    transaction.set(user_ref, {"username": normalized_username}, merge=True)

    if previous_username and previous_username != normalized_username:
        transaction.delete(usernames_collection.document(previous_username))


async def claim_username(user_id: str, username: str) -> str:
    normalized_username = normalize_username(username)
    if not _is_valid_username(normalized_username):
        raise UsernameValidationError(
            f"Username must be at least {MIN_USERNAME_LENGTH} characters long."
        )

    client: firestore.Client = get_firestore()
    usernames_collection = client.collection(USERNAMES_COLLECTION)
    user_ref = client.collection(USERS_COLLECTION).document(user_id)
    username_ref = usernames_collection.document(normalized_username)
    transaction = client.transaction()

    try:
        _claim_username_transaction(
            transaction,
            user_ref,
            username_ref,
            usernames_collection,
            user_id,
            normalized_username,
        )
    except UsernameUnavailableError:
        raise
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to claim username.",
            extra={"user_id": user_id, "username": normalized_username},
        )
        raise FirestoreServiceError("Failed to claim username.") from exc

    return normalized_username
