"""Tests for idempotent send-opportunity tracking in reminder_decision_store."""

import asyncio
import logging
from unittest.mock import MagicMock, patch

import pytest

from app.services.reminder_decision_store import (
    DailySendCountResult,
    build_decision_key,
    get_daily_send_count,
    record_send_decision_if_new,
)


# ---------------------------------------------------------------------------
# build_decision_key
# ---------------------------------------------------------------------------


def test_build_decision_key_format() -> None:
    key = build_decision_key("2026-03-18", "log_first_meal", "2026-03-18T08:20:00Z")
    assert key == "2026-03-18:log_first_meal:2026-03-18T08:20:00Z"


def test_build_decision_key_different_kind_produces_different_key() -> None:
    key_a = build_decision_key("2026-03-18", "log_first_meal", "2026-03-18T08:20:00Z")
    key_b = build_decision_key("2026-03-18", "log_next_meal", "2026-03-18T08:20:00Z")
    assert key_a != key_b


def test_build_decision_key_different_scheduled_at_produces_different_key() -> None:
    key_a = build_decision_key("2026-03-18", "log_first_meal", "2026-03-18T08:20:00Z")
    key_b = build_decision_key("2026-03-18", "log_first_meal", "2026-03-18T09:00:00Z")
    assert key_a != key_b


# ---------------------------------------------------------------------------
# Firestore mock helpers
# ---------------------------------------------------------------------------


def _mock_doc(exists: bool, data: dict | None = None) -> MagicMock:
    doc = MagicMock()
    doc.exists = exists
    doc.to_dict.return_value = data if data else {}
    return doc


def _patch_firestore(doc_mock: MagicMock):
    """Patch get_firestore so _daily_stats_document().get() returns *doc_mock*."""
    mock_ref = MagicMock()
    mock_ref.get.return_value = doc_mock
    mock_ref.set.return_value = None

    mock_client = MagicMock()
    mock_client.collection.return_value.document.return_value.collection.return_value.document.return_value = mock_ref

    return patch(
        "app.services.reminder_decision_store.get_firestore",
        return_value=mock_client,
    ), mock_ref


# ---------------------------------------------------------------------------
# get_daily_send_count — structured result tests
# ---------------------------------------------------------------------------


def test_get_daily_send_count_returns_result_with_degraded_false_for_missing_doc() -> None:
    doc = _mock_doc(exists=False)
    patcher, _ = _patch_firestore(doc)

    with patcher:
        result = asyncio.run(get_daily_send_count("user-1", "2026-03-18"))

    assert isinstance(result, DailySendCountResult)
    assert result.count == 0
    assert result.degraded is False


def test_get_daily_send_count_returns_stored_value_with_degraded_false() -> None:
    doc = _mock_doc(exists=True, data={"sendCount": 2})
    patcher, _ = _patch_firestore(doc)

    with patcher:
        result = asyncio.run(get_daily_send_count("user-1", "2026-03-18"))

    assert result.count == 2
    assert result.degraded is False


def test_get_daily_send_count_returns_fallback_with_degraded_true_on_read_failure() -> None:
    doc = _mock_doc(exists=False)
    patcher, mock_ref = _patch_firestore(doc)
    mock_ref.get.side_effect = Exception("Firestore read failed")

    with patcher:
        result = asyncio.run(get_daily_send_count("user-1", "2026-03-18"))

    assert result.count == 0
    assert result.degraded is True


def test_get_daily_send_count_emits_structured_log_on_success(caplog) -> None:
    doc = _mock_doc(exists=True, data={"sendCount": 1})
    patcher, _ = _patch_firestore(doc)

    with patcher, caplog.at_level(logging.DEBUG, logger="app.services.reminder_decision_store"):
        asyncio.run(get_daily_send_count("user-1", "2026-03-18"))

    store_logs = [r for r in caplog.records if "reminder.store.read_count" in r.message]
    assert len(store_logs) == 1
    assert store_logs[0].operation == "read_count"
    assert store_logs[0].store_mode == "normal"
    assert store_logs[0].count == 1


def test_get_daily_send_count_emits_degraded_log_on_failure(caplog) -> None:
    doc = _mock_doc(exists=False)
    patcher, mock_ref = _patch_firestore(doc)
    mock_ref.get.side_effect = Exception("Firestore read failed")

    with patcher, caplog.at_level(logging.WARNING, logger="app.services.reminder_decision_store"):
        asyncio.run(get_daily_send_count("user-1", "2026-03-18"))

    degraded_logs = [r for r in caplog.records if "reminder.store.read_count.failed" in r.message]
    assert len(degraded_logs) == 1
    assert degraded_logs[0].operation == "read_count"
    assert degraded_logs[0].store_mode == "degraded"
    assert degraded_logs[0].fallback_count == 0


# ---------------------------------------------------------------------------
# record_send_decision_if_new — idempotency tests
# ---------------------------------------------------------------------------


def test_first_send_increments_count() -> None:
    """First unique send opportunity for a day should increment the counter."""
    doc = _mock_doc(exists=False)
    patcher, mock_ref = _patch_firestore(doc)

    with patcher:
        result = asyncio.run(
            record_send_decision_if_new(
                "user-1", "2026-03-18", "log_first_meal", "2026-03-18T08:20:00Z"
            )
        )

    assert result is True
    mock_ref.set.assert_called_once()
    call_args = mock_ref.set.call_args
    payload = call_args[0][0]
    assert "sendCount" in payload
    assert "emittedDecisionKeys" in payload


def test_duplicate_send_does_not_increment_count() -> None:
    """Second identical send for same (dayKey, kind, scheduledAtUtc) must be a no-op."""
    decision_key = "2026-03-18:log_first_meal:2026-03-18T08:20:00Z"
    doc = _mock_doc(
        exists=True,
        data={
            "sendCount": 1,
            "emittedDecisionKeys": [decision_key],
        },
    )
    patcher, mock_ref = _patch_firestore(doc)

    with patcher:
        result = asyncio.run(
            record_send_decision_if_new(
                "user-1", "2026-03-18", "log_first_meal", "2026-03-18T08:20:00Z"
            )
        )

    assert result is False
    mock_ref.set.assert_not_called()


def test_different_kind_same_day_increments_count() -> None:
    """A different reminder kind on the same day is a new opportunity."""
    existing_key = "2026-03-18:log_first_meal:2026-03-18T08:20:00Z"
    doc = _mock_doc(
        exists=True,
        data={
            "sendCount": 1,
            "emittedDecisionKeys": [existing_key],
        },
    )
    patcher, mock_ref = _patch_firestore(doc)

    with patcher:
        result = asyncio.run(
            record_send_decision_if_new(
                "user-1", "2026-03-18", "log_next_meal", "2026-03-18T13:00:00Z"
            )
        )

    assert result is True
    mock_ref.set.assert_called_once()


def test_same_kind_different_scheduled_at_increments_count() -> None:
    """Same kind but different scheduledAtUtc = different opportunity."""
    existing_key = "2026-03-18:log_first_meal:2026-03-18T08:20:00Z"
    doc = _mock_doc(
        exists=True,
        data={
            "sendCount": 1,
            "emittedDecisionKeys": [existing_key],
        },
    )
    patcher, mock_ref = _patch_firestore(doc)

    with patcher:
        result = asyncio.run(
            record_send_decision_if_new(
                "user-1", "2026-03-18", "log_first_meal", "2026-03-18T09:00:00Z"
            )
        )

    assert result is True
    mock_ref.set.assert_called_once()


def test_firestore_write_failure_returns_false_and_does_not_raise() -> None:
    """Write failure is best-effort — must not propagate."""
    doc = _mock_doc(exists=False)
    patcher, mock_ref = _patch_firestore(doc)
    mock_ref.set.side_effect = Exception("Firestore write failed")

    with patcher:
        result = asyncio.run(
            record_send_decision_if_new(
                "user-1", "2026-03-18", "log_first_meal", "2026-03-18T08:20:00Z"
            )
        )

    assert result is False


def test_firestore_read_failure_in_record_returns_false() -> None:
    """If we can't read the doc to check for duplicates, fail gracefully."""
    doc = _mock_doc(exists=False)
    patcher, mock_ref = _patch_firestore(doc)
    mock_ref.get.side_effect = Exception("Firestore read failed")

    with patcher:
        result = asyncio.run(
            record_send_decision_if_new(
                "user-1", "2026-03-18", "log_first_meal", "2026-03-18T08:20:00Z"
            )
        )

    assert result is False


def test_write_failure_emits_degraded_log(caplog) -> None:
    """Write failure must emit a structured warning with store_mode=degraded."""
    doc = _mock_doc(exists=False)
    patcher, mock_ref = _patch_firestore(doc)
    mock_ref.set.side_effect = Exception("Firestore write failed")

    with patcher, caplog.at_level(logging.WARNING, logger="app.services.reminder_decision_store"):
        asyncio.run(
            record_send_decision_if_new(
                "user-1", "2026-03-18", "log_first_meal", "2026-03-18T08:20:00Z"
            )
        )

    degraded_logs = [r for r in caplog.records if "reminder.store.write_decision.failed" in r.message]
    assert len(degraded_logs) == 1
    assert degraded_logs[0].operation == "write_decision"
    assert degraded_logs[0].store_mode == "degraded"


# ---------------------------------------------------------------------------
# Integration scenario: cap based on unique opportunities
# ---------------------------------------------------------------------------


def test_cap_reflects_unique_opportunities_not_call_count() -> None:
    """Simulates the core scenario: repeated fetches for the same send
    opportunity should not inflate the counter toward the cap."""
    from datetime import UTC, datetime

    import json
    from pathlib import Path

    from app.schemas.nutrition_state import NutritionStateResponse
    from app.services.reminder_rule_engine import (
        DAILY_REMINDER_CAP,
        ReminderActivityInput,
        ReminderContextInput,
        ReminderPreferencesInput,
        evaluate_reminder_decision,
    )

    FIXTURES_DIR = Path(__file__).parent / "contract_fixtures"
    state = NutritionStateResponse.model_validate(
        json.loads((FIXTURES_DIR / "nutrition_state.json").read_text(encoding="utf-8"))
    )
    state.quality.mealsLogged = 1
    state.quality.dataCompletenessScore = 1.0

    # With daily_send_count=1, engine should still send
    decision_below_cap = evaluate_reminder_decision(
        state=state,
        preferences=ReminderPreferencesInput(),
        activity=ReminderActivityInput(daily_send_count=1),
        context=ReminderContextInput(
            now_local=datetime(2026, 3, 18, 13, 0, tzinfo=UTC)
        ),
    )
    assert decision_below_cap.decision == "send"

    # With daily_send_count=DAILY_REMINDER_CAP, engine must suppress
    decision_at_cap = evaluate_reminder_decision(
        state=state,
        preferences=ReminderPreferencesInput(),
        activity=ReminderActivityInput(daily_send_count=DAILY_REMINDER_CAP),
        context=ReminderContextInput(
            now_local=datetime(2026, 3, 18, 13, 0, tzinfo=UTC)
        ),
    )
    assert decision_at_cap.decision == "suppress"
    assert "frequency_cap_reached" in decision_at_cap.reasonCodes
