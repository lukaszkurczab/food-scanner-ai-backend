from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from app.core.exceptions import FirestoreServiceError
from app.main import app
from app.services.streak_service import StreakValidationError

client = TestClient(app)


def test_get_streak_returns_backend_payload(mocker: MockerFixture, auth_headers) -> None:
    get_streak = mocker.patch(
        "app.api.routes.streaks.streak_service.get_streak",
        return_value={"current": 4, "lastDate": "2026-03-03"},
    )

    response = client.get("/api/v1/users/me/streak", headers=auth_headers("user-1"))

    assert response.status_code == 200
    assert response.json() == {
        "current": 4,
        "lastDate": "2026-03-03",
        "awardedBadgeIds": [],
    }
    get_streak.assert_called_once_with("user-1")


def test_post_ensure_streak_returns_updated_payload(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    ensure_streak = mocker.patch(
        "app.api.routes.streaks.streak_service.ensure_streak",
        return_value=({"current": 0, "lastDate": None}, []),
    )

    response = client.post(
        "/api/v1/users/me/streak/ensure",
        json={"dayKey": "2026-03-03"},
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "current": 0,
        "lastDate": None,
        "awardedBadgeIds": [],
    }
    ensure_streak.assert_called_once_with("user-1", "2026-03-03")


def test_post_reset_streak_if_missed_returns_updated_payload(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    reset_streak_if_missed = mocker.patch(
        "app.api.routes.streaks.streak_service.reset_streak_if_missed",
        return_value=({"current": 0, "lastDate": "2026-03-01"}, []),
    )

    response = client.post(
        "/api/v1/users/me/streak/reset-if-missed",
        json={"dayKey": "2026-03-03"},
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "current": 0,
        "lastDate": "2026-03-01",
        "awardedBadgeIds": [],
    }
    reset_streak_if_missed.assert_called_once_with("user-1", "2026-03-03")


def test_post_recalculate_streak_returns_awarded_badges(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    recalculate_streak = mocker.patch(
        "app.api.routes.streaks.streak_service.recalculate_streak",
        return_value=(
            {"current": 7, "lastDate": "2026-03-03"},
            ["streak_7"],
        ),
    )

    response = client.post(
        "/api/v1/users/me/streak/recalculate",
        json={
            "dayKey": "2026-03-03",
            "todaysKcal": 1600,
            "targetKcal": 2000,
            "thresholdPct": 0.8,
        },
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "current": 7,
        "lastDate": "2026-03-03",
        "awardedBadgeIds": ["streak_7"],
    }
    recalculate_streak.assert_called_once_with(
        user_id="user-1",
        day_key="2026-03-03",
        todays_kcal=1600.0,
        target_kcal=2000.0,
        threshold_pct=0.8,
    )


def test_post_recalculate_streak_returns_422_for_schema_invalid_day_key(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch(
        "app.api.routes.streaks.streak_service.recalculate_streak",
        side_effect=StreakValidationError("Invalid day key."),
    )

    response = client.post(
        "/api/v1/users/me/streak/recalculate",
        json={
            "dayKey": "bad",
            "todaysKcal": 1600,
            "targetKcal": 2000,
            "thresholdPct": 0.8,
        },
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 422


def test_get_streak_returns_500_for_firestore_errors(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch(
        "app.api.routes.streaks.streak_service.get_streak",
        side_effect=FirestoreServiceError("boom"),
    )

    response = client.get("/api/v1/users/me/streak", headers=auth_headers("user-1"))

    assert response.status_code == 500
    assert response.json() == {"detail": "Database error"}
