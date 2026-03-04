import asyncio

import pytest
from google.api_core.exceptions import GoogleAPICallError
from pytest_mock import MockerFixture

from app.core.exceptions import FirestoreServiceError
from app.services import username_service
from app.services.username_service import (
    UsernameUnavailableError,
    UsernameValidationError,
)


class FakeTransaction:
    def __init__(self) -> None:
        self._id = b"transaction-id"
        self._max_attempts = 1
        self._read_only = False
        self.set_calls: list[tuple[object, dict[str, object], bool | None]] = []
        self.delete_calls: list[object] = []

    def _begin(self, *args, **kwargs) -> None:
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
    previous_username_ref = mocker.Mock()

    def collection_side_effect(name: str):
        if name == "users":
            return users_collection_ref
        if name == "usernames":
            return usernames_collection_ref
        raise AssertionError(f"Unexpected collection {name}")

    client.collection.side_effect = collection_side_effect
    users_collection_ref.document.return_value = user_ref
    usernames_collection_ref.document.side_effect = lambda key: (
        previous_username_ref if key == "neo" else username_ref
    )

    return (
        client,
        users_collection_ref,
        usernames_collection_ref,
        user_ref,
        username_ref,
        previous_username_ref,
    )


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


def test_is_username_available_returns_true_for_missing_document(
    mocker: MockerFixture,
) -> None:
    (
        client,
        _users_collection_ref,
        usernames_collection_ref,
        _user_ref,
        username_ref,
        _previous_username_ref,
    ) = _build_client(mocker)
    username_ref.get.return_value = _build_snapshot(mocker, exists=False)
    mocker.patch("app.services.username_service.get_firestore", return_value=client)

    normalized_username, available = asyncio.run(
        username_service.is_username_available(" Morpheus ")
    )

    usernames_collection_ref.document.assert_called_once_with("morpheus")
    username_ref.get.assert_called_once_with()
    assert normalized_username == "morpheus"
    assert available is True


def test_is_username_available_returns_true_for_same_owner(
    mocker: MockerFixture,
) -> None:
    (
        client,
        _users_collection_ref,
        _usernames_collection_ref,
        _user_ref,
        username_ref,
        _previous_username_ref,
    ) = _build_client(mocker)
    username_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={"uid": "user-1"},
    )
    mocker.patch("app.services.username_service.get_firestore", return_value=client)

    normalized_username, available = asyncio.run(
        username_service.is_username_available("Morpheus", current_user_id="user-1")
    )

    assert normalized_username == "morpheus"
    assert available is True


def test_is_username_available_returns_false_for_short_username() -> None:
    normalized_username, available = asyncio.run(
        username_service.is_username_available("ab")
    )

    assert normalized_username == "ab"
    assert available is False


def test_claim_username_creates_new_mapping_for_user(mocker: MockerFixture) -> None:
    (
        client,
        users_collection_ref,
        usernames_collection_ref,
        user_ref,
        username_ref,
        _previous_username_ref,
    ) = _build_client(mocker)
    transaction = FakeTransaction()
    client.transaction.return_value = transaction
    username_ref.get.return_value = _build_snapshot(mocker, exists=False)
    user_ref.get.return_value = _build_snapshot(mocker, exists=False)
    mocker.patch("app.services.username_service.get_firestore", return_value=client)

    normalized_username = asyncio.run(
        username_service.claim_username("user-1", " Trinity ")
    )

    users_collection_ref.document.assert_called_once_with("user-1")
    usernames_collection_ref.document.assert_any_call("trinity")
    assert normalized_username == "trinity"
    assert transaction.set_calls == [
        (username_ref, {"uid": "user-1"}, True),
        (user_ref, {"username": "trinity"}, True),
    ]
    assert transaction.delete_calls == []


def test_claim_username_deletes_previous_mapping_when_username_changes(
    mocker: MockerFixture,
) -> None:
    (
        client,
        _users_collection_ref,
        usernames_collection_ref,
        user_ref,
        username_ref,
        previous_username_ref,
    ) = _build_client(mocker)
    transaction = FakeTransaction()
    client.transaction.return_value = transaction
    username_ref.get.return_value = _build_snapshot(mocker, exists=False)
    user_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={"username": "neo"},
    )
    mocker.patch("app.services.username_service.get_firestore", return_value=client)

    normalized_username = asyncio.run(
        username_service.claim_username("user-1", "Morpheus")
    )

    assert normalized_username == "morpheus"
    usernames_collection_ref.document.assert_any_call("neo")
    assert transaction.delete_calls == [previous_username_ref]


def test_claim_username_raises_for_taken_username(mocker: MockerFixture) -> None:
    (
        client,
        _users_collection_ref,
        _usernames_collection_ref,
        _user_ref,
        username_ref,
        _previous_username_ref,
    ) = _build_client(mocker)
    transaction = FakeTransaction()
    client.transaction.return_value = transaction
    username_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={"uid": "other-user"},
    )
    mocker.patch("app.services.username_service.get_firestore", return_value=client)

    with pytest.raises(UsernameUnavailableError):
        asyncio.run(username_service.claim_username("user-1", "morpheus"))

    assert transaction.set_calls == []
    assert transaction.delete_calls == []


def test_claim_username_raises_for_invalid_username() -> None:
    with pytest.raises(UsernameValidationError):
        asyncio.run(username_service.claim_username("user-1", "ab"))


def test_claim_username_wraps_firestore_errors(mocker: MockerFixture) -> None:
    (
        client,
        _users_collection_ref,
        _usernames_collection_ref,
        _user_ref,
        username_ref,
        _previous_username_ref,
    ) = _build_client(mocker)
    client.transaction.return_value = FakeTransaction()
    username_ref.get.side_effect = GoogleAPICallError("boom")
    mocker.patch("app.services.username_service.get_firestore", return_value=client)

    with pytest.raises(FirestoreServiceError):
        asyncio.run(username_service.claim_username("user-1", "morpheus"))
