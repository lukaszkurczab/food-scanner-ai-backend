from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from app.core.exceptions import FirestoreServiceError
from app.main import app

client = TestClient(app)


def test_post_reconcile_notification_plan_returns_backend_payload(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    get_plan = mocker.patch(
        "app.api.routes.notifications.notification_plan_service.get_notification_plan",
        return_value=(
            "friendly",
            [],
        ),
    )

    response = client.post(
        "/api/v1/users/me/notifications/reconcile-plan",
        json={
            "startIso": "2026-03-03T00:00:00.000Z",
            "endIso": "2026-03-03T23:59:59.999Z",
        },
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "aiStyle": "friendly",
        "plans": [],
    }
    get_plan.assert_called_once_with(
        "user-1",
        start_iso="2026-03-03T00:00:00.000Z",
        end_iso="2026-03-03T23:59:59.999Z",
    )


def test_post_reconcile_notification_plan_returns_500_for_firestore_errors(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch(
        "app.api.routes.notifications.notification_plan_service.get_notification_plan",
        side_effect=FirestoreServiceError("boom"),
    )

    response = client.post(
        "/api/v1/users/me/notifications/reconcile-plan",
        json={
            "startIso": "2026-03-03T00:00:00.000Z",
            "endIso": "2026-03-03T23:59:59.999Z",
        },
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "Database error"}



def test_get_notifications_returns_backend_payload(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    list_notifications = mocker.patch(
        "app.api.routes.notifications.notification_service.list_notifications",
        return_value=[
            {
                "id": "n-1",
                "type": "day_fill",
                "name": "Keep logging",
                "text": None,
                "time": {"hour": 12, "minute": 0},
                "days": [0, 1, 2, 3, 4, 5, 6],
                "enabled": True,
                "createdAt": 1,
                "updatedAt": 2,
                "mealKind": None,
                "kcalByHour": None,
            }
        ],
    )

    response = client.get(
        "/api/v1/users/me/notifications",
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "id": "n-1",
                "type": "day_fill",
                "name": "Keep logging",
                "text": None,
                "time": {"hour": 12, "minute": 0},
                "days": [0, 1, 2, 3, 4, 5, 6],
                "enabled": True,
                "createdAt": 1,
                "updatedAt": 2,
                "mealKind": None,
                "kcalByHour": None,
            }
        ],
    }
    list_notifications.assert_called_once_with("user-1")


def test_post_notification_upsert_returns_backend_payload(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    upsert_notification = mocker.patch(
        "app.api.routes.notifications.notification_service.upsert_notification",
        return_value={
            "id": "n-1",
            "type": "day_fill",
            "name": "Keep logging",
            "text": None,
            "time": {"hour": 12, "minute": 0},
            "days": [0, 1, 2, 3, 4, 5, 6],
            "enabled": True,
            "createdAt": 1,
            "updatedAt": 2,
            "mealKind": None,
            "kcalByHour": None,
        },
    )

    payload = {
        "id": "n-1",
        "type": "day_fill",
        "name": "Keep logging",
        "text": None,
        "time": {"hour": 12, "minute": 0},
        "days": [0, 1, 2, 3, 4, 5, 6],
        "enabled": True,
        "createdAt": 1,
        "updatedAt": 2,
        "mealKind": None,
        "kcalByHour": None,
    }

    response = client.post(
        "/api/v1/users/me/notifications",
        json=payload,
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "item": payload,
        "updated": True,
    }
    upsert_notification.assert_called_once_with("user-1", payload)


def test_post_delete_notification_returns_backend_payload(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    delete_notification = mocker.patch(
        "app.api.routes.notifications.notification_service.delete_notification",
        return_value=None,
    )

    response = client.post(
        "/api/v1/users/me/notifications/n-1/delete",
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "notificationId": "n-1",
        "deleted": True,
    }
    delete_notification.assert_called_once_with("user-1", "n-1")


def test_get_notification_prefs_returns_backend_payload(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    get_notification_prefs = mocker.patch(
        "app.api.routes.notifications.notification_service.get_notification_prefs",
        return_value={"smartRemindersEnabled": True, "motivationEnabled": True, "daysAhead": 7},
    )

    response = client.get(
        "/api/v1/users/me/notifications/preferences",
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "notifications": {
            "smartRemindersEnabled": True,
            "motivationEnabled": True,
            "statsEnabled": None,
            "weekdays0to6": None,
            "daysAhead": 7,
            "quietHours": None,
        },
    }
    get_notification_prefs.assert_called_once_with("user-1")


def test_post_notification_prefs_returns_backend_payload(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    update_notification_prefs = mocker.patch(
        "app.api.routes.notifications.notification_service.update_notification_prefs",
        return_value={
            "smartRemindersEnabled": False,
            "motivationEnabled": False,
            "statsEnabled": True,
        },
    )

    response = client.post(
        "/api/v1/users/me/notifications/preferences",
        json={
            "notifications": {
                "smartRemindersEnabled": False,
                "motivationEnabled": False,
                "statsEnabled": True,
            }
        },
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "notifications": {
            "smartRemindersEnabled": False,
            "motivationEnabled": False,
            "statsEnabled": True,
            "weekdays0to6": None,
            "daysAhead": None,
            "quietHours": None,
        },
        "updated": True,
    }
    update_notification_prefs.assert_called_once_with(
        "user-1",
        {
            "smartRemindersEnabled": False,
            "motivationEnabled": False,
            "statsEnabled": True,
        },
    )
