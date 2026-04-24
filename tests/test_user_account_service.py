import asyncio
from typing import Any
from unittest.mock import ANY

import pytest
from google.api_core.exceptions import GoogleAPICallError
from pytest_mock import MockerFixture

from app.core.exceptions import FirestoreServiceError
from app.services import user_account_service
from app.services.user_account_service import (
    AvatarMetadataValidationError,
    EmailValidationError,
    OnboardingUsernameUnavailableError,
    OnboardingValidationError,
    UserProfileValidationError,
)


class FakeTransaction:
    def __init__(self) -> None:
        self._id = b"transaction-id"
        self._max_attempts = 1
        self._read_only = False
        self.set_calls: list[tuple[object, dict[str, object], bool | None]] = []
        self.delete_calls: list[object] = []

    def _begin(self, *args: Any, **kwargs: Any) -> None:
        return None

    def _commit(self) -> list[object]:
        return []

    def _rollback(self) -> None:
        return None

    def _clean_up(self) -> None:
        return None

    def set(
        self,
        document_ref: object,
        data: dict[str, object],
        merge: bool | None = None,
    ) -> None:
        self.set_calls.append((document_ref, data, merge))

    def delete(self, document_ref: object) -> None:
        self.delete_calls.append(document_ref)


def _build_client(mocker: MockerFixture):
    client = mocker.Mock()
    users_collection_ref = mocker.Mock()
    usernames_collection_ref = mocker.Mock()
    user_ref = mocker.Mock()
    username_ref = mocker.Mock()

    def collection_side_effect(name: str):
        if name == "users":
            return users_collection_ref
        if name == "usernames":
            return usernames_collection_ref
        raise AssertionError(f"Unexpected collection {name}")

    client.collection.side_effect = collection_side_effect
    client.batch.return_value = mocker.Mock()
    users_collection_ref.document.return_value = user_ref
    usernames_collection_ref.document.return_value = username_ref

    return client, users_collection_ref, usernames_collection_ref, user_ref, username_ref


def _build_snapshot(
    mocker: MockerFixture,
    *,
    exists: bool,
    data: dict[str, object] | None = None,
):
    snapshot = mocker.Mock()
    snapshot.exists = exists
    snapshot.to_dict.return_value = data or {}
    return snapshot


def test_set_email_pending_updates_user_document(mocker: MockerFixture) -> None:
    client, users_collection_ref, _usernames_collection_ref, user_ref, _username_ref = (
        _build_client(mocker)
    )
    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)

    normalized_email = asyncio.run(
        user_account_service.set_email_pending("user-1", " new@example.com ")
    )

    users_collection_ref.document.assert_called_once_with("user-1")
    user_ref.set.assert_called_once_with({"emailPending": "new@example.com"}, merge=True)
    assert normalized_email == "new@example.com"


def test_set_email_pending_raises_for_invalid_email() -> None:
    with pytest.raises(EmailValidationError):
        asyncio.run(user_account_service.set_email_pending("user-1", "bad"))


def test_set_email_pending_wraps_firestore_errors(mocker: MockerFixture) -> None:
    client, _users_collection_ref, _usernames_collection_ref, user_ref, _username_ref = (
        _build_client(mocker)
    )
    user_ref.set.side_effect = GoogleAPICallError("boom")
    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)

    with pytest.raises(FirestoreServiceError):
        asyncio.run(user_account_service.set_email_pending("user-1", "new@example.com"))


def test_delete_account_data_deletes_subcollections_username_and_user_doc(
    mocker: MockerFixture,
) -> None:
    client, _users_collection_ref, usernames_collection_ref, user_ref, username_ref = (
        _build_client(mocker)
    )
    meals_collection_ref = mocker.Mock()
    my_meals_collection_ref = mocker.Mock()
    legacy_chat_collection_ref = mocker.Mock()
    notifications_collection_ref = mocker.Mock()
    prefs_collection_ref = mocker.Mock()
    notif_meta_collection_ref = mocker.Mock()
    feedback_collection_ref = mocker.Mock()
    badges_collection_ref = mocker.Mock()
    streak_collection_ref = mocker.Mock()
    billing_collection_ref = mocker.Mock()
    chat_threads_collection_ref = mocker.Mock()
    meals_doc_1 = mocker.Mock()
    meals_doc_2 = mocker.Mock()
    my_meal_doc = mocker.Mock()
    legacy_chat_doc = mocker.Mock()
    notification_doc = mocker.Mock()
    prefs_doc = mocker.Mock()
    notif_meta_doc = mocker.Mock()
    feedback_doc = mocker.Mock()
    feedback_doc.to_dict.return_value = {}
    badge_doc = mocker.Mock()
    streak_doc = mocker.Mock()
    chat_thread_doc = mocker.Mock()
    chat_thread_messages_collection_ref = mocker.Mock()
    chat_thread_message_doc = mocker.Mock()

    def collection_side_effect(name: str):
        if name == "meals":
            return meals_collection_ref
        if name == "myMeals":
            return my_meals_collection_ref
        if name == "chat_messages":
            return legacy_chat_collection_ref
        if name == "notifications":
            return notifications_collection_ref
        if name == "prefs":
            return prefs_collection_ref
        if name == "notif_meta":
            return notif_meta_collection_ref
        if name == "feedback":
            return feedback_collection_ref
        if name == "badges":
            return badges_collection_ref
        if name == "streak":
            return streak_collection_ref
        if name == "billing":
            return billing_collection_ref
        if name == "chat_threads":
            return chat_threads_collection_ref
        raise AssertionError(f"Unexpected subcollection {name}")

    user_ref.collection.side_effect = collection_side_effect
    meals_collection_ref.stream.return_value = [meals_doc_1, meals_doc_2]
    my_meals_collection_ref.stream.return_value = [my_meal_doc]
    legacy_chat_collection_ref.stream.return_value = [legacy_chat_doc]
    notifications_collection_ref.stream.return_value = [notification_doc]
    prefs_collection_ref.stream.return_value = [prefs_doc]
    notif_meta_collection_ref.stream.return_value = [notif_meta_doc]
    feedback_collection_ref.stream.return_value = [feedback_doc]
    badges_collection_ref.stream.return_value = [badge_doc]
    streak_collection_ref.stream.return_value = [streak_doc]
    billing_collection_ref.stream.return_value = []
    chat_threads_collection_ref.stream.return_value = [chat_thread_doc]
    chat_thread_doc.reference.collection.return_value = chat_thread_messages_collection_ref
    chat_thread_messages_collection_ref.stream.return_value = [chat_thread_message_doc]
    user_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={"username": "neo"},
    )
    batch_1 = mocker.Mock()
    batch_2 = mocker.Mock()
    batch_3 = mocker.Mock()
    batch_4 = mocker.Mock()
    batch_5 = mocker.Mock()
    batch_6 = mocker.Mock()
    batch_7 = mocker.Mock()
    batch_8 = mocker.Mock()
    batch_9 = mocker.Mock()
    batch_10 = mocker.Mock()
    batch_11 = mocker.Mock()
    client.batch.side_effect = [
        batch_1,
        batch_2,
        batch_3,
        batch_4,
        batch_5,
        batch_6,
        batch_7,
        batch_8,
        batch_9,
        batch_10,
        batch_11,
    ]
    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)
    bucket = mocker.Mock()
    avatar_blob = mocker.Mock()
    meal_blob = mocker.Mock()
    my_meal_blob = mocker.Mock()
    bucket.list_blobs.side_effect = [
        [avatar_blob],
        [meal_blob],
        [my_meal_blob],
    ]
    mocker.patch("app.services.user_account_service.get_storage_bucket", return_value=bucket)

    asyncio.run(user_account_service.delete_account_data("user-1"))

    batch_1.delete.assert_any_call(meals_doc_1.reference)
    batch_1.delete.assert_any_call(meals_doc_2.reference)
    batch_1.commit.assert_called_once_with()
    batch_2.delete.assert_called_once_with(my_meal_doc.reference)
    batch_2.commit.assert_called_once_with()
    batch_3.delete.assert_called_once_with(legacy_chat_doc.reference)
    batch_3.commit.assert_called_once_with()
    batch_4.delete.assert_called_once_with(notification_doc.reference)
    batch_4.commit.assert_called_once_with()
    batch_5.delete.assert_called_once_with(prefs_doc.reference)
    batch_5.commit.assert_called_once_with()
    batch_6.delete.assert_called_once_with(notif_meta_doc.reference)
    batch_6.commit.assert_called_once_with()
    batch_7.delete.assert_called_once_with(feedback_doc.reference)
    batch_7.commit.assert_called_once_with()
    batch_8.delete.assert_called_once_with(badge_doc.reference)
    batch_8.commit.assert_called_once_with()
    batch_9.delete.assert_called_once_with(streak_doc.reference)
    batch_9.commit.assert_called_once_with()
    batch_10.delete.assert_called_once_with(chat_thread_message_doc.reference)
    batch_10.commit.assert_called_once_with()
    batch_11.delete.assert_called_once_with(chat_thread_doc.reference)
    batch_11.commit.assert_called_once_with()
    usernames_collection_ref.document.assert_called_once_with("neo")
    username_ref.delete.assert_called_once_with()
    user_ref.delete.assert_called_once_with()
    bucket.list_blobs.assert_any_call(prefix="avatars/user-1/")
    bucket.list_blobs.assert_any_call(prefix="meals/user-1/")
    bucket.list_blobs.assert_any_call(prefix="myMeals/user-1/")
    avatar_blob.delete.assert_called_once_with()
    meal_blob.delete.assert_called_once_with()
    my_meal_blob.delete.assert_called_once_with()


def test_delete_account_data_wraps_firestore_errors(mocker: MockerFixture) -> None:
    client, _users_collection_ref, _usernames_collection_ref, user_ref, _username_ref = (
        _build_client(mocker)
    )
    user_ref.get.side_effect = GoogleAPICallError("boom")
    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)

    with pytest.raises(FirestoreServiceError):
        asyncio.run(user_account_service.delete_account_data("user-1"))


def test_set_avatar_metadata_updates_shared_profile_fields(
    mocker: MockerFixture,
) -> None:
    client, users_collection_ref, _usernames_collection_ref, user_ref, _username_ref = (
        _build_client(mocker)
    )
    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)

    avatar_url, synced_at = asyncio.run(
        user_account_service.set_avatar_metadata("user-1", "https://cdn/avatar.jpg")
    )

    users_collection_ref.document.assert_called_once_with("user-1")
    user_ref.set.assert_called_once_with(
        {
            "avatarUrl": "https://cdn/avatar.jpg",
            "avatarlastSyncedAt": synced_at,
            "avatarLocalPath": ANY,
        },
        merge=True,
    )
    assert avatar_url == "https://cdn/avatar.jpg"
    assert synced_at.endswith("Z")


def test_set_avatar_metadata_raises_for_invalid_url() -> None:
    with pytest.raises(AvatarMetadataValidationError):
        asyncio.run(user_account_service.set_avatar_metadata("user-1", "file:///avatar.jpg"))


def test_upload_avatar_persists_file_and_metadata(mocker: MockerFixture) -> None:
    bucket = mocker.Mock()
    bucket.name = "bucket-name"
    blob = mocker.Mock()
    bucket.blob.return_value = blob
    mocker.patch("app.services.user_account_service.get_storage_bucket", return_value=bucket)
    set_avatar_metadata = mocker.patch(
        "app.services.user_account_service.set_avatar_metadata",
        return_value=("https://cdn/avatar.jpg", "2026-03-03T12:00:00Z"),
    )
    upload = mocker.Mock()
    upload.file = mocker.Mock()
    upload.content_type = "image/jpeg"

    avatar_url, synced_at = asyncio.run(
        user_account_service.upload_avatar("user-1", upload)
    )

    bucket.blob.assert_called_once_with("avatars/user-1/avatar.jpg")
    blob.upload_from_file.assert_called_once_with(
        upload.file,
        content_type="image/jpeg",
    )
    blob.patch.assert_called_once_with()
    upload.file.seek.assert_called_once_with(0)
    upload.file.close.assert_called_once_with()
    set_avatar_metadata.assert_called_once_with("user-1", ANY)
    assert avatar_url == "https://cdn/avatar.jpg"
    assert synced_at == "2026-03-03T12:00:00Z"


def test_get_user_profile_data_returns_profile_document(
    mocker: MockerFixture,
) -> None:
    client, users_collection_ref, _usernames_collection_ref, user_ref, _username_ref = (
        _build_client(mocker)
    )
    user_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={"uid": "user-1", "username": "neo"},
    )
    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)

    profile = asyncio.run(user_account_service.get_user_profile_data("user-1"))

    users_collection_ref.document.assert_called_once_with("user-1")
    assert profile == {"uid": "user-1", "username": "neo"}


def test_get_user_profile_data_returns_none_when_missing(
    mocker: MockerFixture,
) -> None:
    client, _users_collection_ref, _usernames_collection_ref, user_ref, _username_ref = (
        _build_client(mocker)
    )
    user_ref.get.return_value = _build_snapshot(mocker, exists=False)
    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)

    profile = asyncio.run(user_account_service.get_user_profile_data("user-1"))

    assert profile is None


def test_upsert_user_profile_data_bootstraps_server_owned_fields(
    mocker: MockerFixture,
) -> None:
    client, users_collection_ref, _usernames_collection_ref, user_ref, _username_ref = (
        _build_client(mocker)
    )
    user_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={"username": "neo"},
    )
    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)
    sync_streak = mocker.patch("app.services.user_account_service.streak_service.sync_streak_from_meals")

    profile = asyncio.run(
        user_account_service.upsert_user_profile_data(
            "user-1",
            {"language": "pl", "darkTheme": True},
            auth_email="user-1@example.com",
        )
    )

    users_collection_ref.document.assert_called_once_with("user-1")
    user_ref.set.assert_called_once_with(
        {
            "uid": "user-1",
            "email": "user-1@example.com",
            "createdAt": ANY,
            "plan": "free",
            "syncState": "pending",
            "lastLogin": ANY,
            "language": "pl",
            "darkTheme": True,
        },
        merge=True,
    )
    assert profile["uid"] == "user-1"
    assert profile["email"] == "user-1@example.com"
    assert profile["username"] == "neo"
    assert profile["language"] == "pl"
    assert profile["darkTheme"] is True
    sync_streak.assert_not_called()


def test_upsert_user_profile_data_recomputes_streak_when_calorie_target_changes(
    mocker: MockerFixture,
) -> None:
    client, _users_collection_ref, _usernames_collection_ref, user_ref, _username_ref = (
        _build_client(mocker)
    )
    user_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={"username": "neo", "calorieTarget": 2000},
    )
    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)
    sync_streak = mocker.patch("app.services.user_account_service.streak_service.sync_streak_from_meals")

    profile = asyncio.run(
        user_account_service.upsert_user_profile_data(
            "user-1",
            {"calorieTarget": 1800},
            auth_email="user-1@example.com",
        )
    )

    assert profile["calorieTarget"] == 1800
    sync_streak.assert_called_once_with("user-1")


def test_upsert_user_profile_data_rejects_forbidden_fields(
    mocker: MockerFixture,
) -> None:
    client, _users_collection_ref, _usernames_collection_ref, _user_ref, _username_ref = (
        _build_client(mocker)
    )
    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)

    with pytest.raises(UserProfileValidationError):
        asyncio.run(
            user_account_service.upsert_user_profile_data(
                "user-1",
                {"username": "neo"},
                auth_email="user-1@example.com",
            )
        )


def test_initialize_onboarding_profile_creates_atomic_profile_and_username(
    mocker: MockerFixture,
) -> None:
    client, _users_collection_ref, usernames_collection_ref, user_ref, username_ref = (
        _build_client(mocker)
    )
    previous_username_ref = mocker.Mock()
    def _document_for_key(key: str) -> object:
        return previous_username_ref if key == "old-name" else username_ref

    usernames_collection_ref.document.side_effect = _document_for_key
    transaction = FakeTransaction()
    client.transaction.return_value = transaction
    username_ref.get.return_value = _build_snapshot(mocker, exists=False)
    user_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={"username": "old-name"},
    )
    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)

    normalized_username, profile = asyncio.run(
        user_account_service.initialize_onboarding_profile(
            "user-1",
            username=" Neo ",
            language="pl-PL",
            auth_email="user@example.com",
        )
    )

    assert normalized_username == "neo"
    assert profile["uid"] == "user-1"
    assert profile["username"] == "neo"
    assert profile["email"] == "user@example.com"
    assert profile["language"] == "pl"
    assert any(
        call[0] is username_ref and call[1] == {"uid": "user-1"} and call[2] is True
        for call in transaction.set_calls
    )
    assert any(
        call[0] is user_ref and call[2] is True and call[1]["username"] == "neo"
        for call in transaction.set_calls
    )
    assert transaction.delete_calls == [previous_username_ref]


def test_initialize_onboarding_profile_repeated_same_uid_and_username_succeeds(
    mocker: MockerFixture,
) -> None:
    client, _users_collection_ref, _usernames_collection_ref, user_ref, username_ref = (
        _build_client(mocker)
    )
    first_transaction = FakeTransaction()
    second_transaction = FakeTransaction()
    client.transaction.side_effect = [first_transaction, second_transaction]
    username_ref.get.side_effect = [
        _build_snapshot(mocker, exists=False),
        _build_snapshot(mocker, exists=True, data={"uid": "user-1"}),
    ]
    user_ref.get.side_effect = [
        _build_snapshot(mocker, exists=False),
        _build_snapshot(
            mocker,
            exists=True,
            data={"uid": "user-1", "username": "neo", "email": "user@example.com"},
        ),
    ]
    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)

    first_username, first_profile = asyncio.run(
        user_account_service.initialize_onboarding_profile(
            "user-1",
            username="Neo",
            language="pl",
            auth_email="user@example.com",
        )
    )
    second_username, second_profile = asyncio.run(
        user_account_service.initialize_onboarding_profile(
            "user-1",
            username="Neo",
            language="pl",
            auth_email="user@example.com",
        )
    )

    assert first_username == second_username == "neo"
    assert first_profile["uid"] == second_profile["uid"] == "user-1"
    assert first_profile["username"] == second_profile["username"] == "neo"
    assert first_transaction.delete_calls == []
    assert second_transaction.delete_calls == []


def test_initialize_onboarding_profile_is_idempotent_for_same_username_owner(
    mocker: MockerFixture,
) -> None:
    client, _users_collection_ref, _usernames_collection_ref, user_ref, username_ref = (
        _build_client(mocker)
    )
    transaction = FakeTransaction()
    client.transaction.return_value = transaction
    username_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={"uid": "user-1"},
    )
    user_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={"uid": "user-1", "username": "neo", "email": "existing@example.com"},
    )
    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)

    normalized_username, profile = asyncio.run(
        user_account_service.initialize_onboarding_profile(
            "user-1",
            username="neo",
            language="en",
            auth_email=None,
        )
    )

    assert normalized_username == "neo"
    assert profile["username"] == "neo"
    assert profile["email"] == "existing@example.com"
    assert transaction.delete_calls == []


def test_initialize_onboarding_profile_raises_when_username_taken(
    mocker: MockerFixture,
) -> None:
    client, _users_collection_ref, _usernames_collection_ref, _user_ref, username_ref = (
        _build_client(mocker)
    )
    transaction = FakeTransaction()
    client.transaction.return_value = transaction
    username_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={"uid": "other-user"},
    )
    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)

    with pytest.raises(OnboardingUsernameUnavailableError):
        asyncio.run(
            user_account_service.initialize_onboarding_profile(
                "user-1",
                username="neo",
                language="pl",
                auth_email="user@example.com",
            )
        )


def test_initialize_onboarding_profile_rejects_short_username() -> None:
    with pytest.raises(OnboardingValidationError):
        asyncio.run(
            user_account_service.initialize_onboarding_profile(
                "user-1",
                username="ab",
                language="pl",
                auth_email="user@example.com",
            )
        )


def test_get_user_export_data_returns_profile_and_subcollections(
    mocker: MockerFixture,
) -> None:
    client, _users_collection_ref, _usernames_collection_ref, user_ref, _username_ref = (
        _build_client(mocker)
    )
    meals_collection_ref = mocker.Mock()
    my_meals_collection_ref = mocker.Mock()
    notifications_collection_ref = mocker.Mock()
    prefs_collection_ref = mocker.Mock()
    feedback_collection_ref = mocker.Mock()
    chat_threads_collection_ref = mocker.Mock()
    meal_document = mocker.Mock()
    meal_document.to_dict.return_value = {"id": "meal-1"}
    my_meal_document = mocker.Mock()
    my_meal_document.to_dict.return_value = {"id": "saved-1"}
    notification_document = mocker.Mock()
    notification_document.to_dict.return_value = {"id": "notif-1", "enabled": True}
    prefs_document = mocker.Mock()
    prefs_document.to_dict.return_value = {
        "notifications": {"motivationEnabled": True, "daysAhead": 7}
    }
    feedback_document = mocker.Mock()
    feedback_document.to_dict.return_value = {"id": "feedback-1", "message": "hello"}
    chat_thread_document = mocker.Mock()
    chat_thread_document.id = "thread-1"
    chat_thread_document.to_dict.return_value = {"title": "First chat"}
    chat_messages_collection_ref = mocker.Mock()
    chat_document = mocker.Mock()
    chat_document.id = "chat-1"
    chat_document.to_dict.return_value = {"role": "assistant", "content": "hello"}

    def collection_side_effect(name: str):
        if name == "meals":
            return meals_collection_ref
        if name == "myMeals":
            return my_meals_collection_ref
        if name == "notifications":
            return notifications_collection_ref
        if name == "prefs":
            return prefs_collection_ref
        if name == "feedback":
            return feedback_collection_ref
        if name == "chat_threads":
            return chat_threads_collection_ref
        raise AssertionError(f"Unexpected subcollection {name}")

    user_ref.collection.side_effect = collection_side_effect
    meals_collection_ref.stream.return_value = [meal_document]
    my_meals_collection_ref.stream.return_value = [my_meal_document]
    notifications_collection_ref.stream.return_value = [notification_document]
    prefs_collection_ref.stream.return_value = [prefs_document]
    feedback_collection_ref.stream.return_value = [feedback_document]
    chat_threads_collection_ref.stream.return_value = [chat_thread_document]
    chat_thread_document.reference.collection.return_value = chat_messages_collection_ref
    chat_messages_collection_ref.stream.return_value = [chat_document]
    user_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={"uid": "user-1", "username": "neo"},
    )
    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)

    profile, meals, my_meals, chat_messages, notifications, notification_prefs, feedback = asyncio.run(
        user_account_service.get_user_export_data("user-1")
    )

    assert profile == {"uid": "user-1", "username": "neo"}
    assert meals == [{"id": "meal-1"}]
    assert my_meals == [{"id": "saved-1"}]
    assert chat_messages == [
        {
            "id": "chat-1",
            "role": "assistant",
            "content": "hello",
            "threadId": "thread-1",
            "threadTitle": "First chat",
        }
    ]
    assert notifications == [{"id": "notif-1", "enabled": True}]
    assert notification_prefs == {"motivationEnabled": True, "daysAhead": 7}
    assert feedback == [{"id": "feedback-1", "message": "hello"}]


def test_get_user_export_data_wraps_firestore_errors(mocker: MockerFixture) -> None:
    client, _users_collection_ref, _usernames_collection_ref, user_ref, _username_ref = (
        _build_client(mocker)
    )
    user_ref.get.side_effect = GoogleAPICallError("boom")
    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)

    with pytest.raises(FirestoreServiceError):
        asyncio.run(user_account_service.get_user_export_data("user-1"))
