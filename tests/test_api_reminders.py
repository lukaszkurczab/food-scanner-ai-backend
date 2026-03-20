from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from app.core.exceptions import (
    FirestoreServiceError,
    ReminderDecisionContractError,
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


def test_get_reminder_decision_passes_tz_offset_min_to_service(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mock_service = mocker.patch(
        "app.api.routes.reminders.get_reminder_decision",
        return_value=_decision_payload(),
    )

    response = client.get(
        "/api/v2/users/me/reminders/decision?day=2026-03-18&tzOffsetMin=120",
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    mock_service.assert_called_once_with(
        "user-1",
        day_key="2026-03-18",
        tz_offset_min=120,
    )


def test_get_reminder_decision_passes_negative_tz_offset_min(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mock_service = mocker.patch(
        "app.api.routes.reminders.get_reminder_decision",
        return_value=_decision_payload(),
    )

    response = client.get(
        "/api/v2/users/me/reminders/decision?day=2026-03-18&tzOffsetMin=-300",
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    mock_service.assert_called_once_with(
        "user-1",
        day_key="2026-03-18",
        tz_offset_min=-300,
    )


def test_get_reminder_decision_passes_none_when_tz_offset_min_omitted(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mock_service = mocker.patch(
        "app.api.routes.reminders.get_reminder_decision",
        return_value=_decision_payload(),
    )

    response = client.get(
        "/api/v2/users/me/reminders/decision?day=2026-03-18",
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    mock_service.assert_called_once_with(
        "user-1",
        day_key="2026-03-18",
        tz_offset_min=None,
    )


def test_get_reminder_decision_returns_422_for_tz_offset_min_out_of_range(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch(
        "app.api.routes.reminders.get_reminder_decision",
        return_value=_decision_payload(),
    )

    response = client.get(
        "/api/v2/users/me/reminders/decision?day=2026-03-18&tzOffsetMin=900",
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 422


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


def test_get_reminder_decision_returns_500_for_contract_violation_not_400(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    """Internal decision contract violations must surface as 500, not 400.

    A Pydantic validation error inside the rule engine is a backend bug,
    not a client input error — so it must never look like a bad request.
    """
    mocker.patch(
        "app.api.routes.reminders.get_reminder_decision",
        side_effect=ReminderDecisionContractError(
            "Rule engine produced an invalid decision: computedAt length > 20"
        ),
    )

    response = client.get(
        "/api/v2/users/me/reminders/decision?day=2026-03-18",
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "Reminder decision contract violation"}


def test_get_reminder_decision_returns_400_only_for_client_input_errors(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    """ValueError from day_key parsing is still a legitimate 400."""
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


def test_get_reminder_decision_returns_suppress_with_frequency_cap(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    """Endpoint returns a valid suppress decision when frequency cap is reached."""
    mocker.patch(
        "app.api.routes.reminders.get_reminder_decision",
        return_value=ReminderDecision(
            dayKey="2026-03-18",
            computedAt="2026-03-18T14:00:00Z",
            decision="suppress",
            reasonCodes=["frequency_cap_reached"],
            confidence=1.0,
            validUntil="2026-03-18T23:59:59Z",
        ),
    )

    response = client.get(
        "/api/v2/users/me/reminders/decision?day=2026-03-18",
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "suppress"
    assert body["reasonCodes"] == ["frequency_cap_reached"]
    assert body["confidence"] == 1.0
    assert body["kind"] is None
    assert body["scheduledAtUtc"] is None
