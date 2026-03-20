from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from app.core.exceptions import CoachUnavailableError, FirestoreServiceError, StateDisabledError
from app.main import app
from app.schemas.coach import CoachInsight, CoachMeta, CoachResponse

client = TestClient(app)


def test_get_coach_returns_backend_payload(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch(
        "app.api.routes.coach.get_coach_response",
        return_value=CoachResponse(
            dayKey="2026-03-18",
            computedAt="2026-03-18T12:00:00Z",
            source="rules",
            insights=[
                CoachInsight(
                    id="2026-03-18:under_logging",
                    type="under_logging",
                    priority=100,
                    title="Logging looks too light to coach well",
                    body="Log your next meal so today is easier to interpret and adjust.",
                    actionLabel="Log next meal",
                    actionType="log_next_meal",
                    reasonCodes=["valid_logging_days_7_low"],
                    source="rules",
                    validUntil="2026-03-18T23:59:59Z",
                    confidence=0.92,
                    isPositive=False,
                )
            ],
            topInsight=CoachInsight(
                id="2026-03-18:under_logging",
                type="under_logging",
                priority=100,
                title="Logging looks too light to coach well",
                body="Log your next meal so today is easier to interpret and adjust.",
                actionLabel="Log next meal",
                actionType="log_next_meal",
                reasonCodes=["valid_logging_days_7_low"],
                source="rules",
                validUntil="2026-03-18T23:59:59Z",
                confidence=0.92,
                isPositive=False,
            ),
            meta=CoachMeta(
                available=True,
                emptyReason=None,
                isDegraded=False,
            ),
        ),
    )

    response = client.get(
        "/api/v2/users/me/coach?day=2026-03-18",
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json()["dayKey"] == "2026-03-18"
    assert response.json()["topInsight"]["type"] == "under_logging"
    assert response.json()["meta"]["available"] is True


def test_get_coach_returns_400_for_invalid_day(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch(
        "app.api.routes.coach.get_coach_response",
        side_effect=ValueError("Invalid day key. Expected YYYY-MM-DD."),
    )

    response = client.get(
        "/api/v2/users/me/coach?day=2026-13-40",
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid day key. Expected YYYY-MM-DD."}


def test_get_coach_returns_503_when_state_is_disabled(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch(
        "app.api.routes.coach.get_coach_response",
        side_effect=StateDisabledError("disabled"),
    )

    response = client.get("/api/v2/users/me/coach", headers=auth_headers("user-1"))

    assert response.status_code == 503
    assert response.json() == {"detail": "Coach insights are unavailable"}


def test_get_coach_returns_503_when_habits_foundation_is_unavailable(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch(
        "app.api.routes.coach.get_coach_response",
        side_effect=CoachUnavailableError("unavailable"),
    )

    response = client.get("/api/v2/users/me/coach", headers=auth_headers("user-1"))

    assert response.status_code == 503
    assert response.json() == {"detail": "Coach insights are unavailable"}


def test_get_coach_returns_500_for_backend_failures(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch(
        "app.api.routes.coach.get_coach_response",
        side_effect=FirestoreServiceError("firestore failed"),
    )

    response = client.get("/api/v2/users/me/coach", headers=auth_headers("user-1"))

    assert response.status_code == 500
    assert response.json() == {"detail": "Failed to compute coach insights"}
