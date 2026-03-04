import asyncio

import pytest
from google.api_core.exceptions import GoogleAPICallError
from pytest_mock import MockerFixture

from app.core.exceptions import FirestoreServiceError
from app.services import streak_service
from app.services.streak_service import StreakValidationError


def _build_client(mocker: MockerFixture):
    client = mocker.Mock()
    transaction = mocker.Mock()
    streak_collection_ref = mocker.Mock()
    badge_collection_ref = mocker.Mock()
    user_ref = mocker.Mock()
    streak_ref = mocker.Mock()

    client.transaction.return_value = transaction
    client.collection.return_value = mocker.Mock(
        document=mocker.Mock(return_value=user_ref)
    )
    user_ref.collection.side_effect = lambda name: (
        streak_collection_ref if name == "streak" else badge_collection_ref
    )
    streak_collection_ref.document.return_value = streak_ref

    return client, transaction, streak_ref


def test_get_streak_returns_backend_normalized_document(mocker: MockerFixture) -> None:
    client, _transaction, streak_ref = _build_client(mocker)
    snapshot = mocker.Mock()
    snapshot.exists = True
    snapshot.to_dict.return_value = {"current": 3, "lastDate": "2026-03-03"}
    streak_ref.get.return_value = snapshot
    mocker.patch("app.services.streak_service.get_firestore", return_value=client)

    streak = asyncio.run(streak_service.get_streak("user-1"))

    assert streak == {"current": 3, "lastDate": "2026-03-03"}


def test_get_streak_returns_init_when_document_is_missing(mocker: MockerFixture) -> None:
    client, _transaction, streak_ref = _build_client(mocker)
    snapshot = mocker.Mock()
    snapshot.exists = False
    streak_ref.get.return_value = snapshot
    mocker.patch("app.services.streak_service.get_firestore", return_value=client)

    streak = asyncio.run(streak_service.get_streak("user-1"))

    assert streak == {"current": 0, "lastDate": None}


def test_ensure_streak_raises_for_invalid_day_key() -> None:
    with pytest.raises(StreakValidationError):
        asyncio.run(streak_service.ensure_streak("user-1", "bad"))


def test_ensure_streak_returns_transaction_result_and_awarded_badges(
    mocker: MockerFixture,
) -> None:
    client, transaction, streak_ref = _build_client(mocker)
    ensure_tx = mocker.patch(
        "app.services.streak_service._ensure_streak_transaction",
        return_value={"current": 7, "lastDate": "2026-03-03"},
    )
    award_badges = mocker.patch(
        "app.services.streak_service._award_streak_badges",
        return_value=["streak_7"],
    )
    mocker.patch("app.services.streak_service.get_firestore", return_value=client)

    streak, awarded = asyncio.run(
        streak_service.ensure_streak("user-1", "2026-03-03")
    )

    ensure_tx.assert_called_once_with(transaction, streak_ref)
    award_badges.assert_called_once_with(client, "user-1", 7)
    assert streak == {"current": 7, "lastDate": "2026-03-03"}
    assert awarded == ["streak_7"]


def test_reset_streak_if_missed_calls_transaction_and_awards_badges(
    mocker: MockerFixture,
) -> None:
    client, transaction, streak_ref = _build_client(mocker)
    reset_tx = mocker.patch(
        "app.services.streak_service._reset_streak_if_missed_transaction",
        return_value={"current": 0, "lastDate": "2026-03-01"},
    )
    award_badges = mocker.patch(
        "app.services.streak_service._award_streak_badges",
        return_value=[],
    )
    mocker.patch("app.services.streak_service.get_firestore", return_value=client)

    streak, awarded = asyncio.run(
        streak_service.reset_streak_if_missed("user-1", "2026-03-03")
    )

    reset_tx.assert_called_once_with(transaction, streak_ref, "2026-03-03")
    award_badges.assert_called_once_with(client, "user-1", 0)
    assert streak == {"current": 0, "lastDate": "2026-03-01"}
    assert awarded == []


def test_recalculate_streak_calls_transaction_and_awards_badges(
    mocker: MockerFixture,
) -> None:
    client, transaction, streak_ref = _build_client(mocker)
    recalculate_tx = mocker.patch(
        "app.services.streak_service._recalculate_streak_transaction",
        return_value={"current": 8, "lastDate": "2026-03-03"},
    )
    award_badges = mocker.patch(
        "app.services.streak_service._award_streak_badges",
        return_value=["streak_7"],
    )
    mocker.patch("app.services.streak_service.get_firestore", return_value=client)

    streak, awarded = asyncio.run(
        streak_service.recalculate_streak(
            user_id="user-1",
            day_key="2026-03-03",
            todays_kcal=1600,
            target_kcal=2000,
            threshold_pct=0.8,
        )
    )

    recalculate_tx.assert_called_once_with(
        transaction,
        streak_ref,
        "2026-03-03",
        1600,
        2000,
        0.8,
    )
    award_badges.assert_called_once_with(client, "user-1", 8)
    assert streak == {"current": 8, "lastDate": "2026-03-03"}
    assert awarded == ["streak_7"]


def test_get_streak_wraps_firestore_errors(mocker: MockerFixture) -> None:
    client, _transaction, streak_ref = _build_client(mocker)
    streak_ref.get.side_effect = GoogleAPICallError("boom")
    mocker.patch("app.services.streak_service.get_firestore", return_value=client)

    with pytest.raises(FirestoreServiceError):
        asyncio.run(streak_service.get_streak("user-1"))


def test_sync_streak_from_meals_rebuilds_streak_from_daily_meals(
    mocker: MockerFixture,
) -> None:
    client, _transaction, streak_ref = _build_client(mocker)
    user_ref = client.collection.return_value.document.return_value
    meals_collection_ref = user_ref.collection.side_effect("meals")
    meal_snapshot_1 = mocker.Mock()
    meal_snapshot_1.to_dict.return_value = {
        "dayKey": "2026-03-01",
        "deleted": False,
        "totals": {"kcal": 1800},
    }
    meal_snapshot_2 = mocker.Mock()
    meal_snapshot_2.to_dict.return_value = {
        "dayKey": "2026-03-02",
        "deleted": False,
        "totals": {"kcal": 1700},
    }
    meals_collection_ref.where.return_value.stream.return_value = [
        meal_snapshot_1,
        meal_snapshot_2,
    ]
    user_snapshot = mocker.Mock()
    user_snapshot.exists = True
    user_snapshot.to_dict.return_value = {"calorieTarget": 2000}
    user_ref.get.return_value = user_snapshot
    award_badges = mocker.patch(
        "app.services.streak_service._award_streak_badges",
        return_value=[],
    )
    mocker.patch("app.services.streak_service.get_firestore", return_value=client)

    streak, awarded = asyncio.run(
        streak_service.sync_streak_from_meals(
            "user-1",
            reference_day_key="2026-03-03",
        )
    )

    assert streak == {"current": 2, "lastDate": "2026-03-02"}
    assert awarded == []
    streak_ref.set.assert_called_once_with({"current": 2, "lastDate": "2026-03-02"}, merge=True)
    award_badges.assert_called_once_with(client, "user-1", 2)
