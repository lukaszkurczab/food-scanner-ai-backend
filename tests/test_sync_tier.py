"""Tests for /ai/credits/sync-tier fallback reconciliation endpoint."""

from datetime import datetime, timezone
from typing import Literal

from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from app.core.config import settings
from app.main import app
from app.schemas.ai_credits import AiCreditsStatus, CreditCosts

client = TestClient(app)


def _status(
    *,
    tier: Literal["free", "premium"],
    balance: int,
    allocation: int,
    period_start_at: datetime,
    period_end_at: datetime,
) -> AiCreditsStatus:
    return AiCreditsStatus(
        userId="user-1",
        tier=tier,
        balance=balance,
        allocation=allocation,
        periodStartAt=period_start_at,
        periodEndAt=period_end_at,
        costs=CreditCosts(chat=1, textMeal=1, photo=5),
    )


def test_sync_tier_requires_authentication() -> None:
    response = client.post("/api/v1/ai/credits/sync-tier")

    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required"}


def test_sync_tier_repairs_to_premium_when_entitlement_is_active(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch(
        "app.api.routes.ai_credits_sync._fetch_revenuecat_subscriber",
        return_value={
            "subscriber": {
                "entitlements": {
                    "premium": {
                        "purchase_date": "2026-04-14T08:00:00Z",
                        "expires_date": "2026-05-14T08:00:00Z",
                    }
                }
            }
        },
    )
    mocker.patch(
        "app.api.routes.ai_credits_sync.ai_credits_service.get_credits_status",
        return_value=_status(
            tier="free",
            balance=80,
            allocation=100,
            period_start_at=datetime(2026, 3, 23, tzinfo=timezone.utc),
            period_end_at=datetime(2026, 4, 23, tzinfo=timezone.utc),
        ),
    )
    activation = mocker.patch(
        "app.api.routes.ai_credits_sync.ai_credits_service.apply_premium_activation",
        return_value=_status(
            tier="premium",
            balance=800,
            allocation=800,
            period_start_at=datetime(2026, 4, 14, tzinfo=timezone.utc),
            period_end_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
        ),
    )

    response = client.post("/api/v1/ai/credits/sync-tier", headers=auth_headers("user-1"))

    assert response.status_code == 200
    assert response.json()["tier"] == "premium"
    assert response.json()["balance"] == 800
    activation.assert_called_once()
    assert activation.call_args.args[0] == "user-1"
    assert activation.call_args.kwargs["entitlement_id"] == "premium"


def test_sync_tier_transitions_to_free_when_entitlement_is_missing(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch(
        "app.api.routes.ai_credits_sync._fetch_revenuecat_subscriber",
        return_value={"subscriber": {"entitlements": {}}},
    )
    mocker.patch(
        "app.api.routes.ai_credits_sync.ai_credits_service.get_credits_status",
        return_value=_status(
            tier="premium",
            balance=200,
            allocation=800,
            period_start_at=datetime(2026, 4, 14, tzinfo=timezone.utc),
            period_end_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
        ),
    )
    expiration = mocker.patch(
        "app.api.routes.ai_credits_sync.ai_credits_service.apply_premium_expiration",
        return_value=_status(
            tier="free",
            balance=100,
            allocation=100,
            period_start_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
            period_end_at=datetime(2026, 6, 14, tzinfo=timezone.utc),
        ),
    )

    response = client.post("/api/v1/ai/credits/sync-tier", headers=auth_headers("user-1"))

    assert response.status_code == 200
    assert response.json()["tier"] == "free"
    expiration.assert_called_once()


def test_sync_tier_keeps_current_free_cycle_when_no_entitlement(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch(
        "app.api.routes.ai_credits_sync._fetch_revenuecat_subscriber",
        return_value={"subscriber": {"entitlements": {}}},
    )
    current_free_status = _status(
        tier="free",
        balance=95,
        allocation=100,
        period_start_at=datetime(2026, 5, 23, tzinfo=timezone.utc),
        period_end_at=datetime(2026, 6, 23, tzinfo=timezone.utc),
    )
    mocker.patch(
        "app.api.routes.ai_credits_sync.ai_credits_service.get_credits_status",
        return_value=current_free_status,
    )
    expiration = mocker.patch("app.api.routes.ai_credits_sync.ai_credits_service.apply_premium_expiration")

    response = client.post("/api/v1/ai/credits/sync-tier", headers=auth_headers("user-1"))

    assert response.status_code == 200
    assert response.json()["tier"] == "free"
    assert response.json()["balance"] == 95
    expiration.assert_not_called()


def test_sync_tier_returns_503_when_revenuecat_api_key_is_missing(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch.object(settings, "REVENUECAT_API_KEY", "")

    response = client.post("/api/v1/ai/credits/sync-tier", headers=auth_headers("user-1"))

    assert response.status_code == 503
    assert response.json() == {"detail": "RevenueCat API key is not configured"}
