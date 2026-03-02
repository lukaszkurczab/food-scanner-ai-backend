"""Unit tests for the AI usage service and its Firestore transaction flow."""

import asyncio
from datetime import datetime, timezone

import pytest
from google.api_core.exceptions import GoogleAPICallError
from pytest_mock import MockerFixture

from app.core.config import settings
from app.core.exceptions import AiUsageLimitExceededError, FirestoreServiceError
from app.services import ai_usage_service


class FakeTransaction:
    def __init__(self) -> None:
        self._id = b"transaction-id"
        self._max_attempts = 1
        self._read_only = False
        self.set_calls: list[tuple[object, dict[str, object]]] = []

    def _begin(self, *args, **kwargs) -> None:
        return None

    def _commit(self) -> list[object]:
        return []

    def _rollback(self) -> None:
        return None

    def _clean_up(self) -> None:
        return None

    def set(self, document_ref: object, data: dict[str, object]) -> None:
        self.set_calls.append((document_ref, data))


def _build_client(mocker: MockerFixture):
    client = mocker.Mock()
    collection_ref = mocker.Mock()
    document_ref = mocker.Mock()

    client.collection.return_value = collection_ref
    collection_ref.document.return_value = document_ref

    return client, collection_ref, document_ref


def _build_snapshot(mocker: MockerFixture, exists: bool, data: dict[str, object] | None = None):
    snapshot = mocker.Mock()
    snapshot.exists = exists
    snapshot.to_dict.return_value = data or {}
    return snapshot


def test_get_usage_returns_default_when_document_is_missing(mocker: MockerFixture) -> None:
    client, collection_ref, document_ref = _build_client(mocker)
    document_ref.get.return_value = _build_snapshot(mocker, exists=False)

    mocker.patch("app.services.ai_usage_service.get_firestore", return_value=client)
    mocker.patch("app.services.ai_usage_service.get_date_key", return_value="2026-03-02")

    result = asyncio.run(ai_usage_service.get_usage("user-1"))

    client.collection.assert_called_once_with("ai_usage")
    collection_ref.document.assert_called_once_with("user-1-2026-03-02")
    assert result == (0, settings.AI_DAILY_LIMIT_FREE, "2026-03-02")


def test_increment_usage_sets_first_request_of_day_to_one(mocker: MockerFixture) -> None:
    client, collection_ref, document_ref = _build_client(mocker)
    transaction = FakeTransaction()
    document_ref.get.return_value = _build_snapshot(mocker, exists=False)
    client.transaction.return_value = transaction

    mocker.patch("app.services.ai_usage_service.get_firestore", return_value=client)
    mocker.patch("app.services.ai_usage_service.get_date_key", return_value="2026-03-02")

    usage_count, daily_limit, date_key = asyncio.run(ai_usage_service.increment_usage("user-1"))

    client.collection.assert_called_once_with("ai_usage")
    collection_ref.document.assert_called_once_with("user-1-2026-03-02")
    document_ref.get.assert_called_once_with(transaction=transaction)
    assert usage_count == 1
    assert daily_limit == settings.AI_DAILY_LIMIT_FREE
    assert date_key == "2026-03-02"
    assert transaction.set_calls[0][1]["usageCount"] == 1
    assert transaction.set_calls[0][1]["dateKey"] == "2026-03-02"
    assert isinstance(transaction.set_calls[0][1]["updatedAt"], datetime)
    assert transaction.set_calls[0][1]["updatedAt"].tzinfo is timezone.utc


def test_increment_usage_increments_existing_daily_counter(mocker: MockerFixture) -> None:
    client, _collection_ref, document_ref = _build_client(mocker)
    transaction = FakeTransaction()
    document_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={"usageCount": 3, "dateKey": "2026-03-02"},
    )
    client.transaction.return_value = transaction

    mocker.patch("app.services.ai_usage_service.get_firestore", return_value=client)
    mocker.patch("app.services.ai_usage_service.get_date_key", return_value="2026-03-02")

    usage_count, _daily_limit, _date_key = asyncio.run(ai_usage_service.increment_usage("user-1"))

    assert usage_count == 4
    assert transaction.set_calls[0][1]["usageCount"] == 4


def test_increment_usage_resets_counter_when_stored_date_differs(mocker: MockerFixture) -> None:
    client, _collection_ref, document_ref = _build_client(mocker)
    transaction = FakeTransaction()
    document_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={"usageCount": 7, "dateKey": "2026-03-01"},
    )
    client.transaction.return_value = transaction

    mocker.patch("app.services.ai_usage_service.get_firestore", return_value=client)
    mocker.patch("app.services.ai_usage_service.get_date_key", return_value="2026-03-02")

    usage_count, _daily_limit, date_key = asyncio.run(ai_usage_service.increment_usage("user-1"))

    assert usage_count == 1
    assert date_key == "2026-03-02"
    assert transaction.set_calls[0][1]["usageCount"] == 1


def test_increment_usage_raises_when_limit_is_exceeded(mocker: MockerFixture) -> None:
    client, _collection_ref, document_ref = _build_client(mocker)
    transaction = FakeTransaction()
    document_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={"usageCount": settings.AI_DAILY_LIMIT_FREE, "dateKey": "2026-03-02"},
    )
    client.transaction.return_value = transaction

    mocker.patch("app.services.ai_usage_service.get_firestore", return_value=client)
    mocker.patch("app.services.ai_usage_service.get_date_key", return_value="2026-03-02")

    with pytest.raises(AiUsageLimitExceededError):
        asyncio.run(ai_usage_service.increment_usage("user-1"))

    assert transaction.set_calls == []


def test_increment_usage_wraps_firestore_errors(mocker: MockerFixture) -> None:
    client, _collection_ref, document_ref = _build_client(mocker)
    client.transaction.return_value = FakeTransaction()
    document_ref.get.side_effect = GoogleAPICallError("boom")

    mocker.patch("app.services.ai_usage_service.get_firestore", return_value=client)
    mocker.patch("app.services.ai_usage_service.get_date_key", return_value="2026-03-02")

    with pytest.raises(FirestoreServiceError):
        asyncio.run(ai_usage_service.increment_usage("user-1"))
