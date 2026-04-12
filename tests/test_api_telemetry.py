from __future__ import annotations

from datetime import UTC, datetime, timedelta
import logging
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from google.api_core.exceptions import AlreadyExists
from pytest_mock import MockerFixture

from app.api.v2.router import router as v2_router
from app.services import telemetry_service


# ---------------------------------------------------------------------------
# Fake Firestore layer
# ---------------------------------------------------------------------------


class FakeDocumentRef:
    def __init__(self, storage: dict[str, dict[str, object]], document_id: str) -> None:
        self._storage = storage
        self._document_id = document_id

    def create(self, data: dict[str, object]) -> None:
        if self._document_id in self._storage:
            raise AlreadyExists("duplicate document")
        self._storage[self._document_id] = data


class FakeSnapshot:
    def __init__(self, document_id: str, data: dict[str, object]) -> None:
        self.id = document_id
        self._data = data

    def to_dict(self) -> dict[str, object]:
        return self._data


class FakeQuery:
    def __init__(
        self,
        storage: dict[str, dict[str, object]],
        filters: list[tuple[str, str, object]] | None = None,
    ) -> None:
        self._storage = storage
        self._filters = filters or []

    def where(self, field_path: str, op_string: str, value: object) -> "FakeQuery":
        return FakeQuery(self._storage, [*self._filters, (field_path, op_string, value)])

    def stream(self):
        snapshots: list[FakeSnapshot] = []
        for document_id, payload in self._storage.items():
            if _matches_filters(payload, self._filters):
                snapshots.append(FakeSnapshot(document_id, payload))
        return snapshots


def _matches_filters(
    payload: dict[str, object],
    filters: list[tuple[str, str, object]],
) -> bool:
    def _matches_ordered_filter(actual: object | None, expected: object, operator: str) -> bool:
        if isinstance(actual, str) and isinstance(expected, str):
            if operator == ">=":
                return actual >= expected
            if operator == "<=":
                return actual <= expected
            if operator == "<":
                return actual < expected
            return False

        if (
            isinstance(actual, int | float)
            and not isinstance(actual, bool)
            and isinstance(expected, int | float)
            and not isinstance(expected, bool)
        ):
            actual_number = float(actual)
            expected_number = float(expected)
            if operator == ">=":
                return actual_number >= expected_number
            if operator == "<=":
                return actual_number <= expected_number
            if operator == "<":
                return actual_number < expected_number
            return False

        return False

    for field_path, op_string, expected in filters:
        actual = payload.get(field_path)
        if op_string == "==" and actual != expected:
            return False
        if op_string == ">=" and not _matches_ordered_filter(actual, expected, op_string):
            return False
        if op_string == "<=" and not _matches_ordered_filter(actual, expected, op_string):
            return False
        if op_string == "<" and not _matches_ordered_filter(actual, expected, op_string):
            return False
    return True


class FakeCollectionRef:
    def __init__(self, storage: dict[str, dict[str, object]]) -> None:
        self._storage = storage

    def document(self, document_id: str) -> FakeDocumentRef:
        return FakeDocumentRef(self._storage, document_id)

    def where(self, field_path: str, op_string: str, value: object) -> FakeQuery:
        return FakeQuery(self._storage, [(field_path, op_string, value)])


class FakeFirestoreClient:
    def __init__(self) -> None:
        self.storage: dict[str, dict[str, object]] = {}
        self.requested_collections: list[str] = []

    def collection(self, name: str) -> FakeCollectionRef:
        self.requested_collections.append(name)
        return FakeCollectionRef(self.storage)


class FailingDocumentRef:
    """Simulates a Firestore write failure."""

    def create(self, data: dict[str, object]) -> None:
        from google.api_core.exceptions import GoogleAPICallError

        raise GoogleAPICallError("simulated write failure")


class FailingCollectionRef:
    def document(self, document_id: str) -> FailingDocumentRef:
        return FailingDocumentRef()


class FailingFirestoreClient:
    def collection(self, name: str) -> FailingCollectionRef:
        return FailingCollectionRef()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def create_test_client() -> TestClient:
    app = FastAPI()
    app.include_router(v2_router, prefix="/api/v2")
    return TestClient(app)


def build_payload(event_overrides: dict[str, Any] | None = None) -> dict[str, object]:
    event = {
        "eventId": "evt-1",
        "name": "meal_added",
        "ts": "2026-03-18T12:00:00Z",
        "props": {"mealInputMethod": "photo", "ingredientCount": 3},
    }
    if event_overrides:
        event.update(event_overrides)

    return {
        "sessionId": "sess-1",
        "app": {"platform": "ios", "appVersion": "1.2.3", "build": "45"},
        "device": {"locale": "pl-PL", "tzOffsetMin": 60},
        "events": [event],
    }


def setup_telemetry_enabled(mocker: MockerFixture, enabled: bool = True) -> None:
    mocker.patch("app.services.telemetry_service.settings.TELEMETRY_ENABLED", enabled)


def reset_telemetry_state() -> None:
    telemetry_service.reset_rate_limit_state()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_telemetry_batch_accepts_valid_payload(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()

    response = client.post(
        "/api/v2/telemetry/events/batch",
        json=build_payload(),
        headers=auth_headers("user-123"),
    )

    assert response.status_code == 202
    assert response.json() == {
        "acceptedCount": 1,
        "duplicateCount": 0,
        "rejectedCount": 0,
        "rejectedEvents": [],
    }
    assert firestore_client.requested_collections == ["telemetry_events"]
    stored_event = firestore_client.storage["evt-1"]
    assert stored_event["eventId"] == "evt-1"
    assert stored_event["sessionId"] == "sess-1"
    assert stored_event["userHash"] == (
        "fcdec6df4d44dbc637c7c5b58efface52a7f8a88535423430255be0bb89bedd8"
    )
    assert "userId" not in stored_event
    assert stored_event["platform"] == "ios"
    assert stored_event["appVersion"] == "1.2.3"
    assert stored_event["build"] == "45"
    assert stored_event["locale"] == "pl-PL"
    assert stored_event["tzOffsetMin"] == 60
    assert stored_event["props"] == {"mealInputMethod": "photo", "ingredientCount": 3}
    assert stored_event["ts"] == "2026-03-18T12:00:00Z"
    assert isinstance(stored_event["ingestedAt"], str)
    assert stored_event["expiresAt"] is not None


# ---------------------------------------------------------------------------
# Validation / rejection
# ---------------------------------------------------------------------------


def test_telemetry_batch_drops_disallowed_event_names(mocker: MockerFixture) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()

    response = client.post(
        "/api/v2/telemetry/events/batch",
        json=build_payload({"name": "unexpected_event"}),
    )

    assert response.status_code == 202
    assert response.json() == {
        "acceptedCount": 0,
        "duplicateCount": 0,
        "rejectedCount": 1,
        "rejectedEvents": [
            {
                "eventId": "evt-1",
                "name": "unexpected_event",
                "reason": "event_not_allowed",
            }
        ],
    }
    assert firestore_client.storage == {}


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_telemetry_batch_is_idempotent_for_duplicate_event_ids(
    mocker: MockerFixture,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()
    payload = build_payload()

    first = client.post("/api/v2/telemetry/events/batch", json=payload)
    second = client.post("/api/v2/telemetry/events/batch", json=payload)

    assert first.status_code == 202
    assert second.status_code == 202
    assert second.json() == {
        "acceptedCount": 0,
        "duplicateCount": 1,
        "rejectedCount": 0,
        "rejectedEvents": [],
    }
    assert list(firestore_client.storage) == ["evt-1"]


# ---------------------------------------------------------------------------
# Payload limits
# ---------------------------------------------------------------------------


def test_telemetry_batch_rejects_payload_or_batch_that_is_too_large(
    mocker: MockerFixture,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()
    oversized_events = [
        {
            "eventId": f"evt-{index}",
            "name": "meal_added",
            "ts": "2026-03-18T12:00:00Z",
        }
        for index in range(51)
    ]

    batch_too_large = client.post(
        "/api/v2/telemetry/events/batch",
        json={
            "sessionId": "sess-1",
            "app": {"platform": "ios", "appVersion": "1.2.3", "build": "45"},
            "device": {"locale": "pl-PL", "tzOffsetMin": 60},
            "events": oversized_events,
        },
    )
    payload_too_large = client.post(
        "/api/v2/telemetry/events/batch",
        json=build_payload({"props": {"blob": "x" * 3000}}),
    )

    assert batch_too_large.status_code == 422
    assert payload_too_large.status_code == 422
    assert firestore_client.storage == {}


def test_telemetry_batch_rejects_privacy_sensitive_props(
    mocker: MockerFixture,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()

    response = client.post(
        "/api/v2/telemetry/events/batch",
        json=build_payload({"props": {"message": "raw user content"}}),
    )

    assert response.status_code == 422
    assert firestore_client.storage == {}


def test_telemetry_batch_rejects_unknown_props_for_event(
    mocker: MockerFixture,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()

    response = client.post(
        "/api/v2/telemetry/events/batch",
        json=build_payload({"props": {"mealInputMethod": "photo", "screen": "home"}}),
    )

    assert response.status_code == 422
    assert firestore_client.storage == {}


def test_telemetry_batch_accepts_premium_state_evaluated_event(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()

    response = client.post(
        "/api/v2/telemetry/events/batch",
        json=build_payload(
            {
                "name": "premium_state_evaluated",
                "props": {
                    "source": "customer_info",
                    "premium": True,
                    "cacheState": "hit_false",
                    "mismatch": True,
                    "creditsTier": "free",
                },
            }
        ),
        headers=auth_headers("user-123"),
    )

    assert response.status_code == 202
    assert response.json() == {
        "acceptedCount": 1,
        "duplicateCount": 0,
        "rejectedCount": 0,
        "rejectedEvents": [],
    }


def test_telemetry_batch_rejects_invalid_premium_state_source(
    mocker: MockerFixture,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()

    response = client.post(
        "/api/v2/telemetry/events/batch",
        json=build_payload(
            {
                "name": "premium_state_evaluated",
                "props": {
                    "source": "unsupported_source",
                    "premium": False,
                    "cacheState": "miss",
                },
            }
        ),
    )

    assert response.status_code == 422
    assert firestore_client.storage == {}


def test_telemetry_batch_accepts_coach_surface_events(
    mocker: MockerFixture,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()

    payload = {
        "sessionId": "sess-1",
        "app": {"platform": "ios", "appVersion": "1.2.3", "build": "45"},
        "device": {"locale": "pl-PL", "tzOffsetMin": 60},
        "events": [
            {
                "eventId": "evt-coach-1",
                "name": "coach_card_viewed",
                "ts": "2026-03-18T12:00:00Z",
                "props": {
                    "insightType": "under_logging",
                    "actionType": "log_next_meal",
                    "isPositive": False,
                },
            },
            {
                "eventId": "evt-coach-2",
                "name": "coach_card_expanded",
                "ts": "2026-03-18T12:00:10Z",
                "props": {"insightType": "under_logging"},
            },
            {
                "eventId": "evt-coach-3",
                "name": "coach_card_cta_clicked",
                "ts": "2026-03-18T12:00:20Z",
                "props": {
                    "insightType": "under_logging",
                    "actionType": "log_next_meal",
                    "targetScreen": "MealAddMethod",
                },
            },
            {
                "eventId": "evt-coach-4",
                "name": "coach_empty_state_viewed",
                "ts": "2026-03-18T12:00:30Z",
                "props": {"emptyReason": "no_data"},
            },
        ],
    }

    response = client.post("/api/v2/telemetry/events/batch", json=payload)

    assert response.status_code == 202
    assert response.json() == {
        "acceptedCount": 4,
        "duplicateCount": 0,
        "rejectedCount": 0,
        "rejectedEvents": [],
    }
    assert firestore_client.storage["evt-coach-1"]["props"] == {
        "insightType": "under_logging",
        "actionType": "log_next_meal",
        "isPositive": False,
    }


def test_telemetry_batch_accepts_smart_reminder_events(
    mocker: MockerFixture,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()

    payload = {
        "sessionId": "sess-1",
        "app": {"platform": "ios", "appVersion": "1.2.3", "build": "45"},
        "device": {"locale": "pl-PL", "tzOffsetMin": 60},
        "events": [
            {
                "eventId": "evt-reminder-1",
                "name": "smart_reminder_suppressed",
                "ts": "2026-03-18T12:00:00Z",
                "props": {
                    "decision": "suppress",
                    "suppressionReason": "quiet_hours",
                    "confidenceBucket": "high",
                },
            },
            {
                "eventId": "evt-reminder-2",
                "name": "smart_reminder_scheduled",
                "ts": "2026-03-18T12:00:10Z",
                "props": {
                    "reminderKind": "complete_day",
                    "decision": "send",
                    "confidenceBucket": "medium",
                    "scheduledWindow": "evening",
                },
            },
            {
                "eventId": "evt-reminder-3",
                "name": "smart_reminder_noop",
                "ts": "2026-03-18T12:00:20Z",
                "props": {
                    "decision": "noop",
                    "noopReason": "insufficient_signal",
                    "confidenceBucket": "low",
                },
            },
            {
                "eventId": "evt-reminder-4",
                "name": "smart_reminder_decision_failed",
                "ts": "2026-03-18T12:00:30Z",
                "props": {
                    "failureReason": "invalid_payload",
                },
            },
            {
                "eventId": "evt-reminder-5",
                "name": "smart_reminder_schedule_failed",
                "ts": "2026-03-18T12:00:40Z",
                "props": {
                    "reminderKind": "log_next_meal",
                    "decision": "send",
                    "confidenceBucket": "high",
                    "failureReason": "invalid_time",
                },
            },
        ],
    }

    response = client.post("/api/v2/telemetry/events/batch", json=payload)

    assert response.status_code == 202
    assert response.json() == {
        "acceptedCount": 5,
        "duplicateCount": 0,
        "rejectedCount": 0,
        "rejectedEvents": [],
    }
    assert firestore_client.storage["evt-reminder-1"]["props"] == {
        "decision": "suppress",
        "suppressionReason": "quiet_hours",
        "confidenceBucket": "high",
    }
    assert firestore_client.storage["evt-reminder-2"]["props"] == {
        "reminderKind": "complete_day",
        "decision": "send",
        "confidenceBucket": "medium",
        "scheduledWindow": "evening",
    }
    assert firestore_client.storage["evt-reminder-3"]["props"] == {
        "decision": "noop",
        "noopReason": "insufficient_signal",
        "confidenceBucket": "low",
    }
    assert firestore_client.storage["evt-reminder-4"]["props"] == {
        "failureReason": "invalid_payload",
    }
    assert firestore_client.storage["evt-reminder-5"]["props"] == {
        "reminderKind": "log_next_meal",
        "decision": "send",
        "confidenceBucket": "high",
        "failureReason": "invalid_time",
    }


def test_telemetry_batch_rejects_disallowed_coach_props(
    mocker: MockerFixture,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()

    response = client.post(
        "/api/v2/telemetry/events/batch",
        json=build_payload(
            {
                "name": "coach_card_viewed",
                "props": {
                    "insightType": "under_logging",
                    "actionType": "log_next_meal",
                    "title": "Logging looks too light to coach well",
                },
            }
        ),
    )

    assert response.status_code == 422
    assert firestore_client.storage == {}


def test_telemetry_batch_rejects_disallowed_smart_reminder_props(
    mocker: MockerFixture,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()

    response = client.post(
        "/api/v2/telemetry/events/batch",
        json=build_payload(
            {
                "name": "smart_reminder_scheduled",
                "props": {
                    "reminderKind": "log_next_meal",
                    "decision": "send",
                    "reasonCodes": ["habit_window_match"],
                },
            }
        ),
    )

    assert response.status_code == 422
    assert firestore_client.storage == {}


def test_telemetry_batch_rejects_invalid_smart_reminder_enum_values(
    mocker: MockerFixture,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()

    response = client.post(
        "/api/v2/telemetry/events/batch",
        json={
            "sessionId": "sess-1",
            "app": {"platform": "ios", "appVersion": "1.2.3", "build": "45"},
            "device": {"locale": "pl-PL", "tzOffsetMin": 60},
            "events": [
                {
                    "eventId": "evt-reminder-invalid-1",
                    "name": "smart_reminder_scheduled",
                    "ts": "2026-03-18T12:00:00Z",
                    "props": {
                        "reminderKind": "log_next_meal",
                        "decision": "send",
                        "confidenceBucket": "0.80-0.89",
                        "scheduledWindow": "evening",
                    },
                }
            ],
        },
    )

    assert response.status_code == 422
    assert firestore_client.storage == {}


def test_telemetry_batch_rejects_smart_reminder_decision_computed_as_event_not_allowed(
    mocker: MockerFixture,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()

    response = client.post(
        "/api/v2/telemetry/events/batch",
        json=build_payload(
            {
                "name": "smart_reminder_decision_computed",
                "props": {
                    "reminderKind": "log_next_meal",
                    "decision": "send",
                    "confidenceBucket": "0.80-0.89",
                    "scheduledWindow": "12:00-13:30",
                },
            }
        ),
    )

    assert response.status_code == 202
    assert response.json() == {
        "acceptedCount": 0,
        "duplicateCount": 0,
        "rejectedCount": 1,
        "rejectedEvents": [
            {
                "eventId": "evt-1",
                "name": "smart_reminder_decision_computed",
                "reason": "event_not_allowed",
            }
        ],
    }
    assert firestore_client.storage == {}


def test_telemetry_batch_returns_413_when_serialized_batch_payload_is_too_large(
    mocker: MockerFixture,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    mocker.patch.object(telemetry_service, "MAX_BATCH_PAYLOAD_BYTES", 256)
    client = create_test_client()

    response = client.post(
        "/api/v2/telemetry/events/batch",
        json=build_payload(),
    )

    assert response.status_code == 413
    assert response.json() == {"detail": "Telemetry payload is too large"}
    assert firestore_client.storage == {}


# ---------------------------------------------------------------------------
# Feature flag: disabled
# ---------------------------------------------------------------------------


def test_telemetry_batch_returns_503_when_feature_flag_is_disabled(
    mocker: MockerFixture,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=False)
    get_firestore = mocker.patch("app.services.telemetry_service.get_firestore")
    client = create_test_client()

    response = client.post("/api/v2/telemetry/events/batch", json=build_payload())

    assert response.status_code == 503
    assert response.json() == {"detail": "Telemetry ingestion is disabled"}
    get_firestore.assert_not_called()


def test_telemetry_daily_summary_returns_grouped_counts(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    mocker.patch(
        "app.services.telemetry_service.utc_now",
        return_value=telemetry_service.datetime(2026, 3, 18, 23, 59, 59),
    )
    firestore_client = FakeFirestoreClient()
    firestore_client.storage.update(
        {
            "evt-1": {
                "eventId": "evt-1",
                "name": "meal_added",
                "ts": "2026-03-18T09:00:00Z",
                "userHash": telemetry_service._build_user_hash("user-123"),
            },
            "evt-2": {
                "eventId": "evt-2",
                "name": "screen_view",
                "ts": "2026-03-18T10:00:00Z",
                "userHash": telemetry_service._build_user_hash("user-123"),
            },
            "evt-3": {
                "eventId": "evt-3",
                "name": "meal_added",
                "ts": "2026-03-17T10:00:00Z",
                "userHash": telemetry_service._build_user_hash("user-123"),
            },
            "evt-4": {
                "eventId": "evt-4",
                "name": "meal_added",
                "ts": "2026-03-18T10:00:00Z",
                "userHash": telemetry_service._build_user_hash("other-user"),
            },
        }
    )
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()

    response = client.get(
        "/api/v2/telemetry/events/summary/daily?days=7",
        headers=auth_headers("user-123"),
    )

    assert response.status_code == 200
    assert response.json()["days"] == 7
    assert response.json()["buckets"] == [
        {
            "day": "2026-03-17",
            "totalEvents": 1,
            "eventCounts": [{"name": "meal_added", "count": 1}],
        },
        {
            "day": "2026-03-18",
            "totalEvents": 2,
            "eventCounts": [
                {"name": "meal_added", "count": 1},
                {"name": "screen_view", "count": 1},
            ],
        },
    ]


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


def test_telemetry_batch_returns_429_when_rate_limit_is_exceeded(
    mocker: MockerFixture,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    mocker.patch.object(telemetry_service, "RATE_LIMIT_MAX_REQUESTS", 2)
    client = create_test_client()
    payload = build_payload()

    assert client.post("/api/v2/telemetry/events/batch", json=payload).status_code == 202
    assert client.post("/api/v2/telemetry/events/batch", json=build_payload({"eventId": "evt-2"})).status_code == 202
    response = client.post("/api/v2/telemetry/events/batch", json=build_payload({"eventId": "evt-3"}))

    assert response.status_code == 429
    assert response.json() == {"detail": "Too many telemetry requests"}


# ---------------------------------------------------------------------------
# Observability logging — success path
# ---------------------------------------------------------------------------


def test_successful_ingest_logs_batch_summary(
    mocker: MockerFixture,
    auth_headers,
    caplog,
) -> None:
    """telemetry.ingest.ok is logged with counters on every successful batch."""
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()

    with caplog.at_level(logging.INFO, logger="app.services.telemetry_service"):
        response = client.post(
            "/api/v2/telemetry/events/batch",
            json=build_payload(),
            headers=auth_headers("user-obs-1"),
        )

    assert response.status_code == 202
    assert any("telemetry.ingest.ok" in record.message for record in caplog.records)
    summary_record = next(r for r in caplog.records if "telemetry.ingest.ok" in r.message)
    assert summary_record.accepted == 1  # type: ignore[attr-defined]
    assert summary_record.duplicates == 0  # type: ignore[attr-defined]
    assert summary_record.rejected == 0  # type: ignore[attr-defined]
    assert summary_record.session_id == "sess-1"  # type: ignore[attr-defined]
    assert summary_record.user_id == "user-obs-1"  # type: ignore[attr-defined]
    assert summary_record.platform == "ios"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Observability logging — rejected event path
# ---------------------------------------------------------------------------


def test_rejected_event_logs_warning_per_event(
    mocker: MockerFixture,
    caplog,
) -> None:
    """Each disallowed event emits a telemetry.ingest.rejected warning."""
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()

    with caplog.at_level(logging.WARNING, logger="app.services.telemetry_service"):
        response = client.post(
            "/api/v2/telemetry/events/batch",
            json=build_payload({"name": "bad_event"}),
        )

    assert response.status_code == 202
    rejected_records = [r for r in caplog.records if "telemetry.ingest.rejected" in r.message]
    assert len(rejected_records) == 1
    assert rejected_records[0].event_name == "bad_event"  # type: ignore[attr-defined]
    assert rejected_records[0].reason == "event_not_allowed"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Observability logging — rate limit path
# ---------------------------------------------------------------------------


def test_rate_limit_hit_logs_warning(
    mocker: MockerFixture,
    caplog,
) -> None:
    """Rate limit exceeded emits telemetry.ingest.rate_limited warning."""
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    mocker.patch.object(telemetry_service, "RATE_LIMIT_MAX_REQUESTS", 1)
    client = create_test_client()

    # First request succeeds
    client.post("/api/v2/telemetry/events/batch", json=build_payload())

    # Second triggers rate limit
    with caplog.at_level(logging.WARNING, logger="app.services.telemetry_service"):
        response = client.post(
            "/api/v2/telemetry/events/batch",
            json=build_payload({"eventId": "evt-2"}),
        )

    assert response.status_code == 429
    rate_records = [r for r in caplog.records if "telemetry.ingest.rate_limited" in r.message]
    assert len(rate_records) == 1
    assert hasattr(rate_records[0], "bucket_key")


# ---------------------------------------------------------------------------
# Observability logging — Firestore failure path
# ---------------------------------------------------------------------------


def test_firestore_failure_logs_error_and_returns_500(
    mocker: MockerFixture,
    caplog,
) -> None:
    """Firestore write failure emits error log and returns 500."""
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    mocker.patch(
        "app.services.telemetry_service.get_firestore",
        return_value=FailingFirestoreClient(),
    )
    client = create_test_client()

    with caplog.at_level(logging.ERROR, logger="app.services.telemetry_service"):
        response = client.post(
            "/api/v2/telemetry/events/batch",
            json=build_payload(),
        )

    assert response.status_code == 500
    assert response.json() == {"detail": "Failed to ingest telemetry batch"}
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(error_records) >= 1
    assert any("telemetry.ingest.firestore_error" in r.message for r in error_records)


# ---------------------------------------------------------------------------
# Mixed batch — accepted + rejected in one batch
# ---------------------------------------------------------------------------


def test_mixed_batch_logs_both_accepted_and_rejected(
    mocker: MockerFixture,
    auth_headers,
    caplog,
) -> None:
    """A batch with valid and invalid events logs both rejection warnings
    and a summary with correct counters."""
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()

    payload = {
        "sessionId": "sess-mix",
        "app": {"platform": "android", "appVersion": "2.0.0"},
        "device": {},
        "events": [
            {"eventId": "ok-1", "name": "meal_added", "ts": "2026-03-18T12:00:00Z"},
            {"eventId": "bad-1", "name": "unknown_event", "ts": "2026-03-18T12:01:00Z"},
            {"eventId": "ok-2", "name": "screen_view", "ts": "2026-03-18T12:02:00Z"},
        ],
    }

    with caplog.at_level(logging.INFO, logger="app.services.telemetry_service"):
        response = client.post(
            "/api/v2/telemetry/events/batch",
            json=payload,
            headers=auth_headers("user-mix"),
        )

    assert response.status_code == 202
    body = response.json()
    assert body["acceptedCount"] == 2
    assert body["rejectedCount"] == 1
    assert body["duplicateCount"] == 0

    # Verify rejection warning was emitted
    rejected_records = [r for r in caplog.records if "telemetry.ingest.rejected" in r.message]
    assert len(rejected_records) == 1

    # Verify summary was emitted with correct counters
    summary_records = [r for r in caplog.records if "telemetry.ingest.ok" in r.message]
    assert len(summary_records) == 1
    assert summary_records[0].accepted == 2  # type: ignore[attr-defined]
    assert summary_records[0].rejected == 1  # type: ignore[attr-defined]
    assert summary_records[0].events_total == 3  # type: ignore[attr-defined]


# ===========================================================================
# Smart Reminders rollout summary
# ===========================================================================

_USER_HASH_ROLLOUT = (
    "fcdec6df4d44dbc637c7c5b58efface52a7f8a88535423430255be0bb89bedd8"  # sha256("user-123")
)


def _sr_event(
    event_id: str,
    name: str,
    day: str | None = None,
    props: dict[str, Any] | None = None,
    user_hash: str = _USER_HASH_ROLLOUT,
) -> dict[str, object]:
    """Build a raw Firestore document for a smart-reminder event."""
    resolved_day = day or datetime.now(UTC).strftime("%Y-%m-%d")
    return {
        "eventId": event_id,
        "name": name,
        "ts": f"{resolved_day}T00:00:00Z",
        "props": props or {},
        "sessionId": "sess-sr",
        "userHash": user_hash,
        "platform": "ios",
        "appVersion": "1.2.3",
        "build": None,
        "locale": "pl-PL",
        "tzOffsetMin": 60,
        "ingestedAt": f"{resolved_day}T00:00:01Z",
    }


def _seed_sr_events(
    firestore_client: FakeFirestoreClient,
    events: list[dict[str, object]],
) -> None:
    for event in events:
        event_id = str(event["eventId"])
        firestore_client.storage[event_id] = event


# ---------------------------------------------------------------------------
# 1. Summary returns correct aggregates for smart reminder events
# ---------------------------------------------------------------------------


def test_smart_reminder_summary_correct_outcome_aggregates(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    """Outcome counts are correctly aggregated across event types."""
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)

    _seed_sr_events(firestore_client, [
        _sr_event("sr-1", "smart_reminder_scheduled", props={
            "reminderKind": "log_first_meal", "decision": "send",
            "confidenceBucket": "high", "scheduledWindow": "morning",
        }),
        _sr_event("sr-2", "smart_reminder_scheduled", props={
            "reminderKind": "log_next_meal", "decision": "send",
            "confidenceBucket": "medium", "scheduledWindow": "afternoon",
        }),
        _sr_event("sr-3", "smart_reminder_suppressed", props={
            "decision": "suppress", "suppressionReason": "quiet_hours",
            "confidenceBucket": "high",
        }),
        _sr_event("sr-4", "smart_reminder_noop", props={
            "decision": "noop", "noopReason": "insufficient_signal",
            "confidenceBucket": "low",
        }),
        _sr_event("sr-5", "smart_reminder_decision_failed", props={
            "failureReason": "service_unavailable",
        }),
        _sr_event("sr-6", "smart_reminder_schedule_failed", props={
            "reminderKind": "complete_day", "decision": "send",
            "confidenceBucket": "high", "failureReason": "permission_unavailable",
        }),
    ])

    client = create_test_client()
    response = client.get(
        "/api/v2/telemetry/smart-reminders/summary?days=7",
        headers=auth_headers("user-123"),
    )

    assert response.status_code == 200
    body = response.json()

    totals = body["totals"]
    assert totals["scheduled"] == 2
    assert totals["suppressed"] == 1
    assert totals["noop"] == 1
    assert totals["decisionFailed"] == 1
    assert totals["scheduleFailed"] == 1

    # sendRatio = 2 / (2 + 1 + 1) = 0.5
    assert totals["sendRatio"] == 0.5


# ---------------------------------------------------------------------------
# 2. Suppression reasons are correctly grouped
# ---------------------------------------------------------------------------


def test_smart_reminder_summary_groups_suppression_reasons(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    """Suppression reasons are counted and sorted alphabetically."""
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)

    _seed_sr_events(firestore_client, [
        _sr_event("sr-s1", "smart_reminder_suppressed", props={
            "decision": "suppress", "suppressionReason": "quiet_hours",
            "confidenceBucket": "high",
        }),
        _sr_event("sr-s2", "smart_reminder_suppressed", props={
            "decision": "suppress", "suppressionReason": "quiet_hours",
            "confidenceBucket": "medium",
        }),
        _sr_event("sr-s3", "smart_reminder_suppressed", props={
            "decision": "suppress", "suppressionReason": "frequency_cap_reached",
            "confidenceBucket": "high",
        }),
        _sr_event("sr-s4", "smart_reminder_suppressed", props={
            "decision": "suppress", "suppressionReason": "reminders_disabled",
            "confidenceBucket": "low",
        }),
    ])

    client = create_test_client()
    response = client.get(
        "/api/v2/telemetry/smart-reminders/summary?days=7",
        headers=auth_headers("user-123"),
    )

    assert response.status_code == 200
    reasons = response.json()["suppressionReasons"]

    # Sorted alphabetically
    assert reasons == [
        {"reason": "frequency_cap_reached", "count": 1},
        {"reason": "quiet_hours", "count": 2},
        {"reason": "reminders_disabled", "count": 1},
    ]


# ---------------------------------------------------------------------------
# 3. Noop reasons are correctly grouped
# ---------------------------------------------------------------------------


def test_smart_reminder_summary_groups_noop_reasons(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    """Noop reasons are counted and sorted alphabetically."""
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)

    _seed_sr_events(firestore_client, [
        _sr_event("sr-n1", "smart_reminder_noop", props={
            "decision": "noop", "noopReason": "insufficient_signal",
            "confidenceBucket": "low",
        }),
        _sr_event("sr-n2", "smart_reminder_noop", props={
            "decision": "noop", "noopReason": "insufficient_signal",
            "confidenceBucket": "low",
        }),
        _sr_event("sr-n3", "smart_reminder_noop", props={
            "decision": "noop", "noopReason": "day_already_complete",
            "confidenceBucket": "high",
        }),
    ])

    client = create_test_client()
    response = client.get(
        "/api/v2/telemetry/smart-reminders/summary?days=7",
        headers=auth_headers("user-123"),
    )

    assert response.status_code == 200
    reasons = response.json()["noopReasons"]

    assert reasons == [
        {"reason": "day_already_complete", "count": 1},
        {"reason": "insufficient_signal", "count": 2},
    ]


# ---------------------------------------------------------------------------
# 4. Invalid / unrelated telemetry events don't break aggregation
# ---------------------------------------------------------------------------


def test_smart_reminder_summary_ignores_unrelated_events(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    """Non-smart-reminder events in Firestore are silently skipped."""
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)

    _seed_sr_events(firestore_client, [
        # Unrelated events that must be ignored
        _sr_event("unrel-1", "meal_added", props={"mealInputMethod": "photo"}),
        _sr_event("unrel-2", "screen_view", props={"screen": "home"}),
        _sr_event("unrel-3", "session_start", props={"origin": "cold"}),
        # One real smart reminder event
        _sr_event("sr-1", "smart_reminder_scheduled", props={
            "reminderKind": "log_first_meal", "decision": "send",
            "confidenceBucket": "high", "scheduledWindow": "morning",
        }),
    ])

    client = create_test_client()
    response = client.get(
        "/api/v2/telemetry/smart-reminders/summary?days=7",
        headers=auth_headers("user-123"),
    )

    assert response.status_code == 200
    body = response.json()

    totals = body["totals"]
    assert totals["scheduled"] == 1
    assert totals["suppressed"] == 0
    assert totals["noop"] == 0
    assert totals["decisionFailed"] == 0
    assert totals["scheduleFailed"] == 0
    assert body["suppressionReasons"] == []
    assert body["noopReasons"] == []


# ---------------------------------------------------------------------------
# 5. Reminder kinds are grouped from scheduled + schedule_failed
# ---------------------------------------------------------------------------


def test_smart_reminder_summary_groups_reminder_kinds(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    """Reminder kind distribution includes both scheduled and schedule_failed."""
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)

    _seed_sr_events(firestore_client, [
        _sr_event("sr-k1", "smart_reminder_scheduled", props={
            "reminderKind": "log_first_meal", "decision": "send",
            "confidenceBucket": "high", "scheduledWindow": "morning",
        }),
        _sr_event("sr-k2", "smart_reminder_scheduled", props={
            "reminderKind": "log_first_meal", "decision": "send",
            "confidenceBucket": "medium", "scheduledWindow": "morning",
        }),
        _sr_event("sr-k3", "smart_reminder_scheduled", props={
            "reminderKind": "complete_day", "decision": "send",
            "confidenceBucket": "high", "scheduledWindow": "evening",
        }),
        _sr_event("sr-k4", "smart_reminder_schedule_failed", props={
            "reminderKind": "log_next_meal", "decision": "send",
            "confidenceBucket": "high", "failureReason": "schedule_error",
        }),
    ])

    client = create_test_client()
    response = client.get(
        "/api/v2/telemetry/smart-reminders/summary?days=7",
        headers=auth_headers("user-123"),
    )

    assert response.status_code == 200
    kinds = response.json()["reminderKinds"]

    assert kinds == [
        {"kind": "complete_day", "count": 1},
        {"kind": "log_first_meal", "count": 2},
        {"kind": "log_next_meal", "count": 1},
    ]


# ---------------------------------------------------------------------------
# 6. Daily buckets are correctly partitioned
# ---------------------------------------------------------------------------


def test_smart_reminder_summary_daily_buckets(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    """Events are correctly bucketed by day."""
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)

    today = datetime.now(UTC).date()
    previous_day = (today - timedelta(days=1)).isoformat()
    current_day = today.isoformat()

    _seed_sr_events(firestore_client, [
        _sr_event("sr-d1", "smart_reminder_scheduled", day=previous_day, props={
            "reminderKind": "log_first_meal", "decision": "send",
            "confidenceBucket": "high", "scheduledWindow": "morning",
        }),
        _sr_event("sr-d2", "smart_reminder_suppressed", day=previous_day, props={
            "decision": "suppress", "suppressionReason": "quiet_hours",
            "confidenceBucket": "high",
        }),
        _sr_event("sr-d3", "smart_reminder_scheduled", day=current_day, props={
            "reminderKind": "log_next_meal", "decision": "send",
            "confidenceBucket": "medium", "scheduledWindow": "afternoon",
        }),
        _sr_event("sr-d4", "smart_reminder_noop", day=current_day, props={
            "decision": "noop", "noopReason": "insufficient_signal",
            "confidenceBucket": "low",
        }),
    ])

    client = create_test_client()
    response = client.get(
        "/api/v2/telemetry/smart-reminders/summary?days=7",
        headers=auth_headers("user-123"),
    )

    assert response.status_code == 200
    buckets = response.json()["dailyBuckets"]

    assert len(buckets) == 2
    assert buckets[0] == {
        "day": previous_day,
        "scheduled": 1, "suppressed": 1, "noop": 0,
        "decisionFailed": 0, "scheduleFailed": 0,
    }
    assert buckets[1] == {
        "day": current_day,
        "scheduled": 1, "suppressed": 0, "noop": 1,
        "decisionFailed": 0, "scheduleFailed": 0,
    }


# ---------------------------------------------------------------------------
# 7. Send ratio edge case — no outcomes yields null
# ---------------------------------------------------------------------------


def test_smart_reminder_summary_send_ratio_null_when_no_outcomes(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    """sendRatio is null when there are zero send/suppress/noop outcomes."""
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)

    # Only failure events — no send/suppress/noop
    _seed_sr_events(firestore_client, [
        _sr_event("sr-f1", "smart_reminder_decision_failed", props={
            "failureReason": "service_unavailable",
        }),
    ])

    client = create_test_client()
    response = client.get(
        "/api/v2/telemetry/smart-reminders/summary?days=7",
        headers=auth_headers("user-123"),
    )

    assert response.status_code == 200
    assert response.json()["totals"]["sendRatio"] is None


# ---------------------------------------------------------------------------
# 8. Empty result — no events at all
# ---------------------------------------------------------------------------


def test_smart_reminder_summary_empty_when_no_events(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    """An empty Firestore returns zero totals and empty lists."""
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)

    client = create_test_client()
    response = client.get(
        "/api/v2/telemetry/smart-reminders/summary?days=7",
        headers=auth_headers("user-123"),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["totals"]["scheduled"] == 0
    assert body["totals"]["suppressed"] == 0
    assert body["totals"]["sendRatio"] is None
    assert body["suppressionReasons"] == []
    assert body["noopReasons"] == []
    assert body["reminderKinds"] == []
    assert body["dailyBuckets"] == []
