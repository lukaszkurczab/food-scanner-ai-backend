"""Unit tests for AI credits service transaction and rolling-period behavior."""

import asyncio
from datetime import datetime, timezone

import pytest
from pytest_mock import MockerFixture

from app.core.config import settings
from app.core.exceptions import AiCreditsExhaustedError
from app.services import ai_credits_service


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


def _build_client(mocker: MockerFixture):
    client = mocker.Mock()
    credits_collection_ref = mocker.Mock()
    credits_document_ref = mocker.Mock()
    transactions_collection_ref = mocker.Mock()

    def _collection(name: str):
        if name == ai_credits_service.AI_CREDITS_COLLECTION:
            return credits_collection_ref
        if name == ai_credits_service.AI_CREDIT_TRANSACTIONS_COLLECTION:
            return transactions_collection_ref
        raise AssertionError(f"Unexpected collection: {name}")

    client.collection.side_effect = _collection
    client.transaction.return_value = FakeTransaction()
    credits_collection_ref.document.return_value = credits_document_ref
    return client, credits_collection_ref, credits_document_ref, transactions_collection_ref


def _iso_utc(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


def test_get_credits_status_initializes_free_cycle(mocker: MockerFixture) -> None:
    now = _iso_utc(2026, 3, 23)
    client, _collection_ref, document_ref, _transactions_collection_ref = _build_client(mocker)
    transaction = FakeTransaction()
    client.transaction.return_value = transaction
    document_ref.get.return_value = _build_snapshot(mocker, exists=False)

    mocker.patch("app.services.ai_credits_service.get_firestore", return_value=client)
    mocker.patch("app.services.ai_credits_service._utc_now", return_value=now)

    status = asyncio.run(ai_credits_service.get_credits_status("user-1"))

    assert status.tier == "free"
    assert status.balance == settings.AI_CREDITS_FREE
    assert status.allocation == settings.AI_CREDITS_FREE
    assert status.periodStartAt == now
    assert status.periodEndAt == _iso_utc(2026, 4, 23)
    assert transaction.set_calls[0][1]["renewalAnchorSource"] == "free_cycle_start"


def test_deduct_credits_applies_exact_cost(mocker: MockerFixture) -> None:
    now = _iso_utc(2026, 3, 25)
    client, _collection_ref, document_ref, _transactions_collection_ref = _build_client(mocker)
    transaction = FakeTransaction()
    client.transaction.return_value = transaction
    document_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={
            "userId": "user-1",
            "tier": "free",
            "balance": 10,
            "allocation": settings.AI_CREDITS_FREE,
            "periodStartAt": _iso_utc(2026, 3, 23),
            "periodEndAt": _iso_utc(2026, 4, 23),
            "renewalAnchorSource": "free_cycle_start",
            "createdAt": _iso_utc(2026, 3, 23),
            "updatedAt": _iso_utc(2026, 3, 23),
        },
    )

    log_mock = mocker.patch("app.services.ai_credits_service._log_credit_transaction")
    mocker.patch("app.services.ai_credits_service.get_firestore", return_value=client)
    mocker.patch("app.services.ai_credits_service._utc_now", return_value=now)

    status = asyncio.run(ai_credits_service.deduct_credits("user-1", cost=1, action="chat"))

    assert status.balance == 9
    assert status.allocation == settings.AI_CREDITS_FREE
    assert transaction.set_calls[0][1]["balance"] == 9
    log_mock.assert_called_once()


def test_deduct_credits_raises_when_balance_is_insufficient(mocker: MockerFixture) -> None:
    now = _iso_utc(2026, 3, 25)
    client, _collection_ref, document_ref, _transactions_collection_ref = _build_client(mocker)
    transaction = FakeTransaction()
    client.transaction.return_value = transaction
    document_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={
            "userId": "user-1",
            "tier": "free",
            "balance": 0,
            "allocation": settings.AI_CREDITS_FREE,
            "periodStartAt": _iso_utc(2026, 3, 23),
            "periodEndAt": _iso_utc(2026, 4, 23),
            "renewalAnchorSource": "free_cycle_start",
            "createdAt": _iso_utc(2026, 3, 23),
            "updatedAt": _iso_utc(2026, 3, 23),
        },
    )

    log_mock = mocker.patch("app.services.ai_credits_service._log_credit_transaction")
    mocker.patch("app.services.ai_credits_service.get_firestore", return_value=client)
    mocker.patch("app.services.ai_credits_service._utc_now", return_value=now)

    with pytest.raises(AiCreditsExhaustedError):
        asyncio.run(ai_credits_service.deduct_credits("user-1", cost=1, action="chat"))

    assert transaction.set_calls == []
    log_mock.assert_not_called()


def test_refund_credits_increases_balance(mocker: MockerFixture) -> None:
    now = _iso_utc(2026, 3, 26)
    client, _collection_ref, document_ref, _transactions_collection_ref = _build_client(mocker)
    transaction = FakeTransaction()
    client.transaction.return_value = transaction
    document_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={
            "userId": "user-1",
            "tier": "free",
            "balance": 4,
            "allocation": settings.AI_CREDITS_FREE,
            "periodStartAt": _iso_utc(2026, 3, 23),
            "periodEndAt": _iso_utc(2026, 4, 23),
            "renewalAnchorSource": "free_cycle_start",
            "createdAt": _iso_utc(2026, 3, 23),
            "updatedAt": _iso_utc(2026, 3, 23),
        },
    )

    mocker.patch("app.services.ai_credits_service._log_credit_transaction")
    mocker.patch("app.services.ai_credits_service.get_firestore", return_value=client)
    mocker.patch("app.services.ai_credits_service._utc_now", return_value=now)

    status = asyncio.run(ai_credits_service.refund_credits("user-1", cost=3, action="chat_failed"))

    assert status.balance == 7
    assert transaction.set_calls[0][1]["balance"] == 7


def test_refresh_if_period_expired_rolls_to_next_period(mocker: MockerFixture) -> None:
    now = _iso_utc(2026, 4, 24)
    client, _collection_ref, document_ref, _transactions_collection_ref = _build_client(mocker)
    transaction = FakeTransaction()
    client.transaction.return_value = transaction
    document_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={
            "userId": "user-1",
            "tier": "free",
            "balance": 2,
            "allocation": settings.AI_CREDITS_FREE,
            "periodStartAt": _iso_utc(2026, 3, 23),
            "periodEndAt": _iso_utc(2026, 4, 23),
            "renewalAnchorSource": "free_cycle_start",
            "createdAt": _iso_utc(2026, 3, 23),
            "updatedAt": _iso_utc(2026, 3, 23),
        },
    )

    mocker.patch("app.services.ai_credits_service.get_firestore", return_value=client)
    mocker.patch("app.services.ai_credits_service._utc_now", return_value=now)

    status = asyncio.run(ai_credits_service.refresh_if_period_expired("user-1"))

    assert status.balance == settings.AI_CREDITS_FREE
    assert status.periodStartAt == _iso_utc(2026, 4, 23)
    assert status.periodEndAt == _iso_utc(2026, 5, 23)
    assert transaction.set_calls[0][1]["periodStartAt"] == _iso_utc(2026, 4, 23)


def test_refresh_if_period_expired_preserves_rolling_anchor_after_multiple_months(
    mocker: MockerFixture,
) -> None:
    now = _iso_utc(2026, 6, 1)
    client, _collection_ref, document_ref, _transactions_collection_ref = _build_client(mocker)
    transaction = FakeTransaction()
    client.transaction.return_value = transaction
    document_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={
            "userId": "user-1",
            "tier": "free",
            "balance": 3,
            "allocation": settings.AI_CREDITS_FREE,
            "periodStartAt": _iso_utc(2026, 3, 23),
            "periodEndAt": _iso_utc(2026, 4, 23),
            "renewalAnchorSource": "free_cycle_start",
            "createdAt": _iso_utc(2026, 3, 23),
            "updatedAt": _iso_utc(2026, 3, 23),
        },
    )

    mocker.patch("app.services.ai_credits_service.get_firestore", return_value=client)
    mocker.patch("app.services.ai_credits_service._utc_now", return_value=now)

    status = asyncio.run(ai_credits_service.refresh_if_period_expired("user-1"))

    assert status.periodStartAt == _iso_utc(2026, 5, 23)
    assert status.periodEndAt == _iso_utc(2026, 6, 23)
    assert status.balance == settings.AI_CREDITS_FREE


def test_refresh_if_period_expired_clamps_end_of_month_anchor(mocker: MockerFixture) -> None:
    now = _iso_utc(2026, 3, 1)
    client, _collection_ref, document_ref, _transactions_collection_ref = _build_client(mocker)
    transaction = FakeTransaction()
    client.transaction.return_value = transaction
    document_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={
            "userId": "user-1",
            "tier": "free",
            "balance": 5,
            "allocation": settings.AI_CREDITS_FREE,
            "periodStartAt": _iso_utc(2026, 1, 31),
            "periodEndAt": _iso_utc(2026, 2, 28),
            "renewalAnchorSource": "free_cycle_start",
            "createdAt": _iso_utc(2026, 1, 31),
            "updatedAt": _iso_utc(2026, 2, 1),
        },
    )

    mocker.patch("app.services.ai_credits_service.get_firestore", return_value=client)
    mocker.patch("app.services.ai_credits_service._utc_now", return_value=now)

    status = asyncio.run(ai_credits_service.refresh_if_period_expired("user-1"))

    assert status.periodStartAt == _iso_utc(2026, 2, 28)
    assert status.periodEndAt == _iso_utc(2026, 3, 28)


def test_start_premium_cycle_sets_premium_allocation_and_period(mocker: MockerFixture) -> None:
    now = _iso_utc(2026, 4, 14)
    client, _collection_ref, document_ref, _transactions_collection_ref = _build_client(mocker)
    transaction = client.transaction.return_value
    document_ref.get.return_value = _build_snapshot(mocker, exists=False)

    mocker.patch("app.services.ai_credits_service._log_credit_transaction")
    mocker.patch("app.services.ai_credits_service.get_firestore", return_value=client)
    mocker.patch("app.services.ai_credits_service._utc_now", return_value=now)

    status = asyncio.run(
        ai_credits_service.start_premium_cycle(
            "user-1",
            anchor_at=_iso_utc(2026, 4, 14),
            period_end_at=_iso_utc(2026, 5, 14),
        )
    )

    assert len(transaction.set_calls) == 1
    written_document = transaction.set_calls[0][1]
    assert status.tier == "premium"
    assert status.balance == settings.AI_CREDITS_PREMIUM
    assert status.allocation == settings.AI_CREDITS_PREMIUM
    assert status.periodStartAt == _iso_utc(2026, 4, 14)
    assert status.periodEndAt == _iso_utc(2026, 5, 14)
    assert written_document["tier"] == "premium"
    assert written_document["allocation"] == settings.AI_CREDITS_PREMIUM
