from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from app.core.exceptions import FirestoreServiceError
from app.main import app
from app.schemas.weekly_reports import WeeklyReportPeriod, WeeklyReportResponse

client = TestClient(app)


def test_get_weekly_report_returns_backend_payload(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch(
        "app.api.routes.weekly_reports.ai_credits_service.get_credits_status",
        return_value=mocker.Mock(tier="premium"),
    )
    mocker.patch(
        "app.api.routes.weekly_reports.get_weekly_report",
        return_value=WeeklyReportResponse(
            status="insufficient_data",
            period=WeeklyReportPeriod(
                startDay="2026-03-09",
                endDay="2026-03-15",
            ),
            summary="Log a few complete days to unlock a weekly report.",
            insights=[],
            priorities=[],
        ),
    )

    response = client.get(
        "/api/v2/users/me/reports/weekly?weekEnd=2026-03-15",
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "insufficient_data"
    assert response.json()["period"] == {
        "startDay": "2026-03-09",
        "endDay": "2026-03-15",
    }


def test_get_weekly_report_returns_400_for_invalid_week_end(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch(
        "app.api.routes.weekly_reports.ai_credits_service.get_credits_status",
        return_value=mocker.Mock(tier="premium"),
    )
    mocker.patch(
        "app.api.routes.weekly_reports.get_weekly_report",
        side_effect=ValueError("Invalid weekEnd. Expected YYYY-MM-DD."),
    )

    response = client.get(
        "/api/v2/users/me/reports/weekly?weekEnd=2026-13-40",
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid weekEnd. Expected YYYY-MM-DD."}


def test_get_weekly_report_returns_500_for_backend_failures(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch(
        "app.api.routes.weekly_reports.ai_credits_service.get_credits_status",
        return_value=mocker.Mock(tier="premium"),
    )
    mocker.patch(
        "app.api.routes.weekly_reports.get_weekly_report",
        side_effect=FirestoreServiceError("firestore failed"),
    )

    response = client.get(
        "/api/v2/users/me/reports/weekly",
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "Failed to compute weekly report"}


def test_get_weekly_report_returns_403_for_free_users(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    get_weekly_report = mocker.patch("app.api.routes.weekly_reports.get_weekly_report")
    mocker.patch(
        "app.api.routes.weekly_reports.ai_credits_service.get_credits_status",
        return_value=mocker.Mock(tier="free"),
    )

    response = client.get(
        "/api/v2/users/me/reports/weekly?weekEnd=2026-03-15",
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "WEEKLY_REPORT_PREMIUM_REQUIRED"}
    get_weekly_report.assert_not_called()
