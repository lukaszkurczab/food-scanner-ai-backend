from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from google.api_core.exceptions import AlreadyExists
from pytest_mock import MockerFixture

from app.api.v2.router import router as v2_router
from app.services import telemetry_service


class FakeDocumentRef:
    def __init__(self, storage: dict[str, dict[str, object]], document_id: str) -> None:
        self._storage = storage
        self._document_id = document_id

    def create(self, data: dict[str, object]) -> None:
        if self._document_id in self._storage:
            raise AlreadyExists("duplicate document")
        self._storage[self._document_id] = data


class FakeCollectionRef:
    def __init__(self, storage: dict[str, dict[str, object]]) -> None:
        self._storage = storage

    def document(self, document_id: str) -> FakeDocumentRef:
        return FakeDocumentRef(self._storage, document_id)


class FakeFirestoreClient:
    def __init__(self) -> None:
        self.storage: dict[str, dict[str, object]] = {}
        self.requested_collections: list[str] = []

    def collection(self, name: str) -> FakeCollectionRef:
        self.requested_collections.append(name)
        return FakeCollectionRef(self.storage)


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
    assert stored_event["userId"] == "user-123"
    assert stored_event["platform"] == "ios"
    assert stored_event["appVersion"] == "1.2.3"
    assert stored_event["build"] == "45"
    assert stored_event["locale"] == "pl-PL"
    assert stored_event["tzOffsetMin"] == 60
    assert stored_event["props"] == {"mealInputMethod": "photo", "ingredientCount": 3}
    assert stored_event["ts"] == "2026-03-18T12:00:00Z"
    assert isinstance(stored_event["ingestedAt"], str)


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


def test_telemetry_batch_returns_413_when_serialized_batch_payload_is_too_large(
    mocker: MockerFixture,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()
    large_events = [
        {
            "eventId": f"evt-{index}",
            "name": "meal_added",
            "ts": "2026-03-18T12:00:00Z",
            "props": {"blobParts": ["x" * 180 for _ in range(10)]},
        }
        for index in range(50)
    ]

    response = client.post(
        "/api/v2/telemetry/events/batch",
        json={
            "sessionId": "sess-1",
            "app": {"platform": "ios", "appVersion": "1.2.3", "build": "45"},
            "device": {"locale": "pl-PL", "tzOffsetMin": 60},
            "events": large_events,
        },
    )

    assert response.status_code == 413
    assert response.json() == {"detail": "Telemetry payload is too large"}
    assert firestore_client.storage == {}


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
