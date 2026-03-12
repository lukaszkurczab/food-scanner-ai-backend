"""Tests for RevenueCat webhook handling and idempotent premium transitions."""

import asyncio
from datetime import datetime, timezone
from typing import Literal

import pytest
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from app.core.config import settings
from app.main import app
from app.schemas.ai_credits import AiCreditsStatus, CreditCosts
from app.services import ai_credits_service

client = TestClient(app)


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


def _status(
    *,
    tier: Literal["free", "premium"],
    balance: int,
    allocation: int,
    period_start_at: datetime,
    period_end_at: datetime,
) -> AiCreditsStatus:
    return AiCreditsStatus(
        userId="user-1",
        tier=tier,
        balance=balance,
        allocation=allocation,
        periodStartAt=period_start_at,
        periodEndAt=period_end_at,
        costs=CreditCosts(chat=1, textMeal=1, photo=5),
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


def _build_credits_client(mocker: MockerFixture):
    mocked_client = mocker.Mock()
    credits_collection_ref = mocker.Mock()
    credits_document_ref = mocker.Mock()

    mocked_client.collection.return_value = credits_collection_ref
    credits_collection_ref.document.return_value = credits_document_ref

    return mocked_client, credits_collection_ref, credits_document_ref


@pytest.fixture(autouse=True)
def _configure_webhook_secret(mocker: MockerFixture) -> None:
    mocker.patch.object(settings, "REVENUECAT_WEBHOOK_SECRET", "test-webhook-secret")


def test_revenuecat_webhook_rejects_invalid_secret(mocker: MockerFixture) -> None:
    get_credits = mocker.patch(
        "app.api.routes.webhooks.ai_credits_service.get_credits_status",
        return_value=_status(
            tier="free",
            balance=100,
            allocation=100,
            period_start_at=datetime(2026, 3, 23, tzinfo=timezone.utc),
            period_end_at=datetime(2026, 4, 23, tzinfo=timezone.utc),
        ),
    )

    response = client.post(
        "/webhooks/revenuecat",
        headers={"X-RevenueCat-Signature": "bad-secret"},
        json={"event": {"type": "CANCELLATION", "app_user_id": "user-1", "id": "evt-1"}},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid webhook signature"}
    get_credits.assert_not_called()


def test_revenuecat_webhook_handles_initial_purchase(mocker: MockerFixture) -> None:
    activation = mocker.patch(
        "app.api.routes.webhooks.ai_credits_service.apply_premium_activation",
        return_value=_status(
            tier="premium",
            balance=800,
            allocation=800,
            period_start_at=datetime(2026, 4, 14, tzinfo=timezone.utc),
            period_end_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
        ),
    )

    response = client.post(
        "/webhooks/revenuecat",
        headers={"X-RevenueCat-Signature": "test-webhook-secret"},
        json={
            "event": {
                "id": "evt-purchase-1",
                "type": "INITIAL_PURCHASE",
                "app_user_id": "user-1",
                "entitlement_id": "premium",
                "purchased_at": "2026-04-14T08:00:00Z",
                "expiration_at": "2026-05-14T08:00:00Z",
            }
        },
    )

    assert response.status_code == 200
    assert response.json()["tier"] == "premium"
    assert response.json()["balance"] == 800
    activation.assert_called_once()
    assert activation.call_args.args[0] == "user-1"
    assert activation.call_args.kwargs["event_id"] == "evt-purchase-1"
    assert activation.call_args.kwargs["entitlement_id"] == "premium"


def test_revenuecat_webhook_handles_renewal(mocker: MockerFixture) -> None:
    renewal = mocker.patch(
        "app.api.routes.webhooks.ai_credits_service.apply_premium_renewal",
        return_value=_status(
            tier="premium",
            balance=800,
            allocation=800,
            period_start_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
            period_end_at=datetime(2026, 6, 14, tzinfo=timezone.utc),
        ),
    )

    response = client.post(
        "/webhooks/revenuecat",
        headers={"Authorization": "Bearer test-webhook-secret"},
        json={
            "event": {
                "id": "evt-renewal-1",
                "type": "RENEWAL",
                "app_user_id": "user-1",
                "entitlement_id": "premium",
                "purchased_at": "2026-05-14T08:00:00Z",
                "expiration_at": "2026-06-14T08:00:00Z",
            }
        },
    )

    assert response.status_code == 200
    assert response.json()["tier"] == "premium"
    renewal.assert_called_once()
    assert renewal.call_args.kwargs["event_id"] == "evt-renewal-1"


def test_revenuecat_webhook_handles_expiration_transition(mocker: MockerFixture) -> None:
    expiration = mocker.patch(
        "app.api.routes.webhooks.ai_credits_service.apply_premium_expiration",
        return_value=_status(
            tier="free",
            balance=100,
            allocation=100,
            period_start_at=datetime(2026, 6, 14, tzinfo=timezone.utc),
            period_end_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
        ),
    )

    response = client.post(
        "/webhooks/revenuecat",
        headers={"X-RevenueCat-Signature": "test-webhook-secret"},
        json={
            "event": {
                "id": "evt-exp-1",
                "type": "EXPIRATION",
                "app_user_id": "user-1",
                "expiration_at": "2026-06-14T08:00:00Z",
            }
        },
    )

    assert response.status_code == 200
    assert response.json()["tier"] == "free"
    expiration.assert_called_once()
    assert expiration.call_args.kwargs["event_id"] == "evt-exp-1"


def test_revenuecat_webhook_cancellation_does_not_revoke_premium(mocker: MockerFixture) -> None:
    get_credits = mocker.patch(
        "app.api.routes.webhooks.ai_credits_service.get_credits_status",
        return_value=_status(
            tier="premium",
            balance=250,
            allocation=800,
            period_start_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            period_end_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        ),
    )
    expiration = mocker.patch("app.api.routes.webhooks.ai_credits_service.apply_premium_expiration")

    response = client.post(
        "/webhooks/revenuecat",
        headers={"X-RevenueCat-Signature": "test-webhook-secret"},
        json={
            "event": {
                "id": "evt-cancel-1",
                "type": "CANCELLATION",
                "app_user_id": "user-1",
            }
        },
    )

    assert response.status_code == 200
    assert response.json()["tier"] == "premium"
    get_credits.assert_called_once_with("user-1")
    expiration.assert_not_called()


def test_apply_premium_activation_is_idempotent_for_duplicate_event(
    mocker: MockerFixture,
) -> None:
    now = datetime(2026, 5, 14, tzinfo=timezone.utc)
    mock_client, _collection_ref, document_ref = _build_credits_client(mocker)
    transaction = FakeTransaction()
    mock_client.transaction.return_value = transaction
    document_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={
            "userId": "user-1",
            "tier": "premium",
            "balance": 612,
            "allocation": 800,
            "periodStartAt": datetime(2026, 5, 14, tzinfo=timezone.utc),
            "periodEndAt": datetime(2026, 6, 14, tzinfo=timezone.utc),
            "renewalAnchorSource": "premium_activation",
            "revenueCatEntitlementId": "premium",
            "revenueCatExpirationAt": datetime(2026, 6, 14, tzinfo=timezone.utc),
            "lastRevenueCatEventId": "evt-dup-1",
            "createdAt": datetime(2026, 4, 14, tzinfo=timezone.utc),
            "updatedAt": datetime(2026, 5, 14, tzinfo=timezone.utc),
        },
    )

    log_mock = mocker.patch("app.services.ai_credits_service._log_credit_transaction")
    mocker.patch("app.services.ai_credits_service.get_firestore", return_value=mock_client)
    mocker.patch("app.services.ai_credits_service._utc_now", return_value=now)

    status = asyncio.run(
        ai_credits_service.apply_premium_activation(
            "user-1",
            anchor_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
            period_end_at=datetime(2026, 6, 14, tzinfo=timezone.utc),
            event_id="evt-dup-1",
            entitlement_id="premium",
        )
    )

    assert status.tier == "premium"
    assert status.balance == 612
    assert transaction.set_calls == []
    log_mock.assert_not_called()


def test_apply_premium_activation_resets_balance_and_sets_premium_period(
    mocker: MockerFixture,
) -> None:
    now = datetime(2026, 4, 14, tzinfo=timezone.utc)
    mock_client, _collection_ref, document_ref = _build_credits_client(mocker)
    transaction = FakeTransaction()
    mock_client.transaction.return_value = transaction
    document_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={
            "userId": "user-1",
            "tier": "free",
            "balance": 20,
            "allocation": 100,
            "periodStartAt": datetime(2026, 3, 23, tzinfo=timezone.utc),
            "periodEndAt": datetime(2026, 4, 23, tzinfo=timezone.utc),
            "renewalAnchorSource": "free_cycle_start",
            "createdAt": datetime(2026, 3, 23, tzinfo=timezone.utc),
            "updatedAt": datetime(2026, 4, 1, tzinfo=timezone.utc),
        },
    )

    mocker.patch("app.services.ai_credits_service._log_credit_transaction")
    mocker.patch("app.services.ai_credits_service.get_firestore", return_value=mock_client)
    mocker.patch("app.services.ai_credits_service._utc_now", return_value=now)

    status = asyncio.run(
        ai_credits_service.apply_premium_activation(
            "user-1",
            anchor_at=datetime(2026, 4, 14, tzinfo=timezone.utc),
            period_end_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
            event_id="evt-activation-1",
            entitlement_id="premium",
        )
    )

    assert status.tier == "premium"
    assert status.balance == 800
    assert status.allocation == 800
    assert status.periodStartAt == datetime(2026, 4, 14, tzinfo=timezone.utc)
    assert status.periodEndAt == datetime(2026, 5, 14, tzinfo=timezone.utc)
    assert transaction.set_calls[0][1]["lastRevenueCatEventId"] == "evt-activation-1"


def test_apply_premium_renewal_refreshes_to_next_premium_period(
    mocker: MockerFixture,
) -> None:
    now = datetime(2026, 5, 14, tzinfo=timezone.utc)
    mock_client, _collection_ref, document_ref = _build_credits_client(mocker)
    transaction = FakeTransaction()
    mock_client.transaction.return_value = transaction
    document_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={
            "userId": "user-1",
            "tier": "premium",
            "balance": 133,
            "allocation": 800,
            "periodStartAt": datetime(2026, 4, 14, tzinfo=timezone.utc),
            "periodEndAt": datetime(2026, 5, 14, tzinfo=timezone.utc),
            "renewalAnchorSource": "premium_activation",
            "revenueCatEntitlementId": "premium",
            "revenueCatExpirationAt": datetime(2026, 5, 14, tzinfo=timezone.utc),
            "createdAt": datetime(2026, 4, 14, tzinfo=timezone.utc),
            "updatedAt": datetime(2026, 5, 10, tzinfo=timezone.utc),
        },
    )

    mocker.patch("app.services.ai_credits_service._log_credit_transaction")
    mocker.patch("app.services.ai_credits_service.get_firestore", return_value=mock_client)
    mocker.patch("app.services.ai_credits_service._utc_now", return_value=now)

    status = asyncio.run(
        ai_credits_service.apply_premium_renewal(
            "user-1",
            anchor_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
            period_end_at=datetime(2026, 6, 14, tzinfo=timezone.utc),
            event_id="evt-renewal-1",
            entitlement_id="premium",
        )
    )

    assert status.tier == "premium"
    assert status.balance == 800
    assert status.periodStartAt == datetime(2026, 5, 14, tzinfo=timezone.utc)
    assert status.periodEndAt == datetime(2026, 6, 14, tzinfo=timezone.utc)
    assert transaction.set_calls[0][1]["renewalAnchorSource"] == "premium_renewal"


def test_apply_premium_expiration_transitions_to_free_cycle(
    mocker: MockerFixture,
) -> None:
    now = datetime(2026, 6, 14, tzinfo=timezone.utc)
    mock_client, _collection_ref, document_ref = _build_credits_client(mocker)
    transaction = FakeTransaction()
    mock_client.transaction.return_value = transaction
    document_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={
            "userId": "user-1",
            "tier": "premium",
            "balance": 50,
            "allocation": 800,
            "periodStartAt": datetime(2026, 5, 14, tzinfo=timezone.utc),
            "periodEndAt": datetime(2026, 6, 14, tzinfo=timezone.utc),
            "renewalAnchorSource": "premium_renewal",
            "revenueCatEntitlementId": "premium",
            "revenueCatExpirationAt": datetime(2026, 6, 14, tzinfo=timezone.utc),
            "createdAt": datetime(2026, 4, 14, tzinfo=timezone.utc),
            "updatedAt": datetime(2026, 6, 10, tzinfo=timezone.utc),
        },
    )

    mocker.patch("app.services.ai_credits_service._log_credit_transaction")
    mocker.patch("app.services.ai_credits_service.get_firestore", return_value=mock_client)
    mocker.patch("app.services.ai_credits_service._utc_now", return_value=now)

    status = asyncio.run(
        ai_credits_service.apply_premium_expiration(
            "user-1",
            anchor_at=datetime(2026, 6, 14, tzinfo=timezone.utc),
            event_id="evt-expiration-1",
        )
    )

    assert status.tier == "free"
    assert status.balance == 100
    assert status.allocation == 100
    assert status.periodStartAt == datetime(2026, 6, 14, tzinfo=timezone.utc)
    assert status.periodEndAt == datetime(2026, 7, 14, tzinfo=timezone.utc)
    assert transaction.set_calls[0][1]["renewalAnchorSource"] == "premium_expiration_free_cycle_start"
