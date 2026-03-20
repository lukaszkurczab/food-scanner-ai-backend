from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from app.core.exceptions import (
    FirestoreServiceError,
    ReminderUnavailableError,
    SmartRemindersDisabledError,
    StateDisabledError,
)
from app.main import app
from app.schemas.reminders import ReminderDecision

client = TestClient(app)


def _decision_payload() -> ReminderDecision:
    return ReminderDecision(
        dayKey="2026-03-18",
        computedAt="2026-03-18T12:00:00Z",
        decision="send",
        kind="log_next_meal",
        reasonCodes=[
            "preferred_window_today",
            "day_partially_logged",
        ],
        scheduledAtUtc="2026-03-18T18:30:00Z",
        confidence=0.84,
        validUntil="2026-03-18T19:30:00Z",
    )


def test_get_reminder_decision_returns_backend_payload(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch(
        "app.api.routes.reminders.get_reminder_decision",
        return_value=_decision_payload(),
    )

    response = client.get(
        "/api/v2/users/me/reminders/decision?day=2026-03-18",
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "dayKey": "2026-03-18",
        "computedAt": "2026-03-18T12:00:00Z",
        "decision": "send",
        "kind": "log_next_meal",
        "reasonCodes": [
            "preferred_window_today",
            "day_partially_logged",
        ],
        "scheduledAtUtc": "2026-03-18T18:30:00Z",
        "confidence": 0.84,
        "validUntil": "2026-03-18T19:30:00Z",
    }


def test_get_reminder_decision_returns_400_for_invalid_day(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch(
        "app.api.routes.reminders.get_reminder_decision",
        side_effect=ValueError("Invalid day key. Expected YYYY-MM-DD."),
    )

    response = client.get(
        "/api/v2/users/me/reminders/decision?day=2026-13-40",
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid day key. Expected YYYY-MM-DD."}


def test_get_reminder_decision_returns_503_when_feature_is_disabled(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch(
        "app.api.routes.reminders.get_reminder_decision",
        side_effect=SmartRemindersDisabledError("disabled"),
    )

    response = client.get(
        "/api/v2/users/me/reminders/decision",
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Smart reminders are unavailable"}


def test_get_reminder_decision_returns_503_when_required_foundations_are_unavailable(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch(
        "app.api.routes.reminders.get_reminder_decision",
        side_effect=ReminderUnavailableError("unavailable"),
    )

    response = client.get(
        "/api/v2/users/me/reminders/decision",
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Smart reminders are unavailable"}


def test_get_reminder_decision_returns_503_when_state_is_disabled(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch(
        "app.api.routes.reminders.get_reminder_decision",
        side_effect=StateDisabledError("disabled"),
    )

    response = client.get(
        "/api/v2/users/me/reminders/decision",
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Smart reminders are unavailable"}


def test_get_reminder_decision_returns_500_for_backend_failures(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch(
        "app.api.routes.reminders.get_reminder_decision",
        side_effect=FirestoreServiceError("firestore failed"),
    )

    response = client.get(
        "/api/v2/users/me/reminders/decision",
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "Failed to compute reminder decision"}
