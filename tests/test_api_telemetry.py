from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from google.api_core.exceptions import AlreadyExists
from pytest_mock import MockerFixture

from app.api.v2.router import router as v2_router
from app.services import telemetry_service
from tests.types import AuthHeaders, LogCaptureFixture


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
    def create(self, data: dict[str, object]) -> None:
        from google.api_core.exceptions import GoogleAPICallError

        raise GoogleAPICallError("simulated write failure")


class FailingCollectionRef:
    def document(self, document_id: str) -> FailingDocumentRef:
        return FailingDocumentRef()


class FailingFirestoreClient:
    def collection(self, name: str) -> FailingCollectionRef:
        return FailingCollectionRef()


def create_test_client() -> TestClient:
    app = FastAPI()
    app.include_router(v2_router, prefix="/api/v2")
    return TestClient(app)


def build_payload(event_overrides: dict[str, Any] | None = None) -> dict[str, object]:
    event: dict[str, Any] = {
        "eventId": "evt-1",
        "name": "meal_logged",
        "ts": "2026-03-18T12:00:00Z",
        "props": {
            "mealInputMethod": "photo",
            "ingredientCount": 3,
            "source": "ai",
        },
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


def test_telemetry_batch_accepts_valid_payload(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
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
    stored_event = firestore_client.storage["evt-1"]
    assert stored_event["eventId"] == "evt-1"
    assert stored_event["name"] == "meal_logged"
    assert stored_event["sessionId"] == "sess-1"
    assert stored_event["userId"] == "user-123"
    assert stored_event["userHash"] == (
        "fcdec6df4d44dbc637c7c5b58efface52a7f8a88535423430255be0bb89bedd8"
    )
    assert stored_event["platform"] == "ios"
    assert stored_event["appVersion"] == "1.2.3"
    assert stored_event["build"] == "45"
    assert stored_event["locale"] == "pl-PL"
    assert stored_event["tzOffsetMin"] == 60
    assert stored_event["props"] == {
        "mealInputMethod": "photo",
        "ingredientCount": 3,
        "source": "ai",
    }


def test_telemetry_batch_accepts_launch_kpi_events(mocker: MockerFixture) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()

    payload: dict[str, Any] = {
        "sessionId": "sess-1",
        "app": {"platform": "ios", "appVersion": "1.2.3", "build": "45"},
        "device": {"locale": "pl-PL", "tzOffsetMin": 60},
        "events": [
            {
                "eventId": "evt-onboarding",
                "name": "onboarding_completed",
                "ts": "2026-03-18T12:00:00Z",
                "props": {"mode": "first"},
            },
            {
                "eventId": "evt-review",
                "name": "ai_meal_review_saved",
                "ts": "2026-03-18T12:00:10Z",
                "props": {
                    "inputMethod": "photo",
                    "corrected": True,
                    "ingredientCount": 4,
                    "requestId": "run-1",
                },
            },
            {
                "eventId": "evt-paywall",
                "name": "paywall_viewed",
                "ts": "2026-03-18T12:00:20Z",
                "props": {"source": "meal_text_limit"},
            },
            {
                "eventId": "evt-purchase",
                "name": "purchase_completed",
                "ts": "2026-03-18T12:00:30Z",
                "props": {"source": "manage_subscription"},
            },
            {
                "eventId": "evt-entitlement",
                "name": "entitlement_activated",
                "ts": "2026-03-18T12:00:40Z",
                "props": {"source": "purchase", "tier": "premium"},
            },
            {
                "eventId": "evt-weekly",
                "name": "weekly_report_opened",
                "ts": "2026-03-18T12:00:50Z",
                "props": {
                    "reportStatus": "ready",
                    "insightCount": 2,
                    "priorityCount": 2,
                },
            },
            {
                "eventId": "evt-notification",
                "name": "notification_opened",
                "ts": "2026-03-18T12:01:00Z",
                "props": {
                    "notificationType": "meal_reminder",
                    "origin": "system_notifications",
                },
            },
        ],
    }

    response = client.post("/api/v2/telemetry/events/batch", json=payload)

    assert response.status_code == 202
    assert response.json()["acceptedCount"] == 7
    assert response.json()["rejectedCount"] == 0


def test_telemetry_batch_accepts_smart_reminder_events(
    mocker: MockerFixture,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()

    payload: dict[str, Any] = {
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
        ],
    }

    response = client.post("/api/v2/telemetry/events/batch", json=payload)

    assert response.status_code == 202
    assert response.json() == {
        "acceptedCount": 2,
        "duplicateCount": 0,
        "rejectedCount": 0,
        "rejectedEvents": [],
    }


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


def test_telemetry_batch_rejects_invalid_enum_values(
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
                "name": "paywall_viewed",
                "props": {"source": "unsupported_source"},
            }
        ),
    )

    assert response.status_code == 422
    assert firestore_client.storage == {}


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
            "name": "meal_logged",
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

    assert batch_too_large.status_code == 422


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
    auth_headers: AuthHeaders,
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
                "name": "meal_logged",
                "ts": "2026-03-18T09:00:00Z",
                "userHash": telemetry_service._build_user_hash("user-123"),
            },
            "evt-2": {
                "eventId": "evt-2",
                "name": "onboarding_completed",
                "ts": "2026-03-18T10:00:00Z",
                "userHash": telemetry_service._build_user_hash("user-123"),
            },
            "evt-3": {
                "eventId": "evt-3",
                "name": "meal_logged",
                "ts": "2026-03-17T10:00:00Z",
                "userHash": telemetry_service._build_user_hash("user-123"),
            },
            "evt-4": {
                "eventId": "evt-4",
                "name": "meal_logged",
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
            "eventCounts": [{"name": "meal_logged", "count": 1}],
        },
        {
            "day": "2026-03-18",
            "totalEvents": 2,
            "eventCounts": [
                {"name": "meal_logged", "count": 1},
                {"name": "onboarding_completed", "count": 1},
            ],
        },
    ]


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
    assert (
        client.post(
            "/api/v2/telemetry/events/batch",
            json=build_payload({"eventId": "evt-2"}),
        ).status_code
        == 202
    )
    response = client.post(
        "/api/v2/telemetry/events/batch",
        json=build_payload({"eventId": "evt-3"}),
    )

    assert response.status_code == 429
    assert response.json() == {"detail": "Too many telemetry requests"}


def test_successful_ingest_logs_batch_summary(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
    caplog: LogCaptureFixture,
) -> None:
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


def test_rejected_event_logs_warning_per_event(
    mocker: MockerFixture,
    caplog: LogCaptureFixture,
) -> None:
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


def test_rate_limit_hit_logs_warning(
    mocker: MockerFixture,
    caplog: LogCaptureFixture,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    mocker.patch.object(telemetry_service, "RATE_LIMIT_MAX_REQUESTS", 1)
    client = create_test_client()

    client.post("/api/v2/telemetry/events/batch", json=build_payload())

    with caplog.at_level(logging.WARNING, logger="app.services.telemetry_service"):
        response = client.post(
            "/api/v2/telemetry/events/batch",
            json=build_payload({"eventId": "evt-2"}),
        )

    assert response.status_code == 429
    rate_records = [r for r in caplog.records if "telemetry.ingest.rate_limited" in r.message]
    assert len(rate_records) == 1


def test_firestore_failure_logs_error_and_returns_500(
    mocker: MockerFixture,
    caplog: LogCaptureFixture,
) -> None:
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
    assert any(
        "telemetry.ingest.firestore_error" in record.message for record in caplog.records
    )
