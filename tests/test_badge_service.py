import asyncio
from typing import cast

import pytest
from google.api_core.exceptions import GoogleAPICallError
from google.cloud import firestore
from pytest_mock import MockerFixture

from app.core.exceptions import FirestoreServiceError
from app.services import badge_service


class FakeTransaction:
    def __init__(self) -> None:
        self._id = b"transaction-id"
        self._max_attempts = 1
        self._read_only = False
        self.set_calls: list[tuple[object, dict[str, object], bool | None]] = []

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


class FakeSnapshot:
    def __init__(self, exists: bool, data: dict[str, object] | None = None) -> None:
        self.exists = exists
        self._data = data or {}

    def to_dict(self) -> dict[str, object]:
        return dict(self._data)


class FakeDocumentRef:
    def __init__(self, document_id: str, snapshots: dict[str, FakeSnapshot]) -> None:
        self.document_id = document_id
        self._snapshots = snapshots

    def get(self, transaction: object | None = None) -> FakeSnapshot:
        return self._snapshots[self.document_id]


class FakeCollectionRef:
    def __init__(self, snapshots: dict[str, FakeSnapshot]) -> None:
        self._snapshots = snapshots
        self.document_refs: dict[str, FakeDocumentRef] = {}

    def document(self, document_id: str) -> FakeDocumentRef:
        ref = self.document_refs.get(document_id)
        if ref is None:
            ref = FakeDocumentRef(document_id, self._snapshots)
            self.document_refs[document_id] = ref
        return ref


def test_reconcile_premium_badges_transaction_creates_start_badge() -> None:
    snapshots = {
        "premium_start": FakeSnapshot(False),
        "premium_90d": FakeSnapshot(False),
        "premium_365d": FakeSnapshot(False),
        "premium_730d": FakeSnapshot(False),
    }
    transaction = FakeTransaction()
    collection = FakeCollectionRef(snapshots)

    awarded, has_premium = badge_service._reconcile_premium_badges_transaction(
        cast(firestore.Transaction, transaction),
        collection,
        True,
        1_700_000_000_000,
    )

    assert awarded == ["premium_start"]
    assert has_premium is True
    assert transaction.set_calls[0][1]["id"] == "premium_start"


def test_reconcile_premium_badges_transaction_awards_milestones() -> None:
    now_ms = 1_900_000_000_000
    snapshots = {
        "premium_start": FakeSnapshot(True, {"unlockedAt": now_ms - (365 * badge_service.DAY_MS)}),
        "premium_90d": FakeSnapshot(False),
        "premium_365d": FakeSnapshot(False),
        "premium_730d": FakeSnapshot(False),
    }
    transaction = FakeTransaction()
    collection = FakeCollectionRef(snapshots)

    awarded, has_premium = badge_service._reconcile_premium_badges_transaction(
        cast(firestore.Transaction, transaction),
        collection,
        True,
        now_ms,
    )

    assert awarded == ["premium_90d", "premium_365d"]
    assert has_premium is True
    assert [call[1]["id"] for call in transaction.set_calls] == [
        "premium_90d",
        "premium_365d",
    ]


def test_reconcile_premium_badges_transaction_noops_for_non_premium_user() -> None:
    snapshots = {
        "premium_start": FakeSnapshot(True, {"unlockedAt": 1_700_000_000_000}),
        "premium_90d": FakeSnapshot(False),
        "premium_365d": FakeSnapshot(False),
        "premium_730d": FakeSnapshot(False),
    }
    transaction = FakeTransaction()
    collection = FakeCollectionRef(snapshots)

    awarded, has_premium = badge_service._reconcile_premium_badges_transaction(
        cast(firestore.Transaction, transaction),
        collection,
        False,
        1_900_000_000_000,
    )

    assert awarded == []
    assert has_premium is True
    assert transaction.set_calls == []


def test_reconcile_premium_badges_wraps_firestore_errors(
    mocker: MockerFixture,
) -> None:
    client = mocker.Mock()
    transaction = mocker.Mock()
    badges_collection = mocker.Mock()
    user_ref = mocker.Mock()
    client.transaction.return_value = transaction
    client.collection.return_value = mocker.Mock(document=mocker.Mock(return_value=user_ref))
    user_ref.collection.return_value = badges_collection
    mocker.patch("app.services.badge_service.get_firestore", return_value=client)
    mocker.patch(
        "app.services.badge_service._reconcile_premium_badges_transaction",
        side_effect=GoogleAPICallError("boom"),
    )

    with pytest.raises(FirestoreServiceError):
        asyncio.run(
            badge_service.reconcile_premium_badges("user-1", is_premium=True)
        )
