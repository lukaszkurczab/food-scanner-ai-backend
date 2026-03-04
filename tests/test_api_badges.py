from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from app.core.exceptions import FirestoreServiceError
from app.main import app

client = TestClient(app)


def test_get_badges_returns_backend_payload(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    list_badges = mocker.patch(
        "app.api.routes.badges.badge_service.list_badges",
        return_value=[
            {
                "id": "streak_7",
                "type": "streak",
                "label": "7 days streak",
                "milestone": 7,
                "icon": "🔥",
                "color": "#5AA469",
                "unlockedAt": 1,
            }
        ],
    )

    response = client.get(
        "/api/v1/users/me/badges",
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "id": "streak_7",
                "type": "streak",
                "label": "7 days streak",
                "milestone": 7,
                "icon": "🔥",
                "color": "#5AA469",
                "unlockedAt": 1,
            }
        ],
    }
    list_badges.assert_called_once_with("user-1")


def test_post_reconcile_premium_badges_returns_backend_payload(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    reconcile = mocker.patch(
        "app.api.routes.badges.badge_service.reconcile_premium_badges",
        return_value=(["premium_start"], True),
    )

    response = client.post(
        "/api/v1/users/me/badges/premium/reconcile",
        json={"isPremium": True},
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "awardedBadgeIds": ["premium_start"],
        "hasPremiumBadge": True,
        "updated": True,
    }
    reconcile.assert_called_once_with("user-1", is_premium=True, now_ms=None)


def test_post_reconcile_premium_badges_returns_500_for_firestore_errors(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch(
        "app.api.routes.badges.badge_service.reconcile_premium_badges",
        side_effect=FirestoreServiceError("boom"),
    )

    response = client.post(
        "/api/v1/users/me/badges/premium/reconcile",
        json={"isPremium": True},
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "Database error"}


def test_get_badges_returns_500_for_firestore_errors(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch(
        "app.api.routes.badges.badge_service.list_badges",
        side_effect=FirestoreServiceError("boom"),
    )

    response = client.get(
        "/api/v1/users/me/badges",
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "Database error"}
