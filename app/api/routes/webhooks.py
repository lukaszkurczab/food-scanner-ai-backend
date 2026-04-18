from datetime import datetime
import hmac
from typing import cast

from fastapi import APIRouter, Header, HTTPException, status

from app.core.config import settings
from app.core.datetime_utils import parse_flexible_datetime, utc_now
from app.schemas.ai_credits import RevenueCatWebhookPayload
from app.services import ai_credits_service

router = APIRouter()


def _extract_header_secret(authorization: str | None) -> str | None:
    if authorization is None:
        return None
    normalized = authorization.strip()
    if not normalized:
        return None
    if normalized.lower().startswith("bearer "):
        return normalized[7:].strip() or None
    return normalized


def _verify_webhook_secret(
    *,
    authorization: str | None,
) -> None:
    expected_secret = settings.REVENUECAT_WEBHOOK_SECRET.strip()
    if not expected_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="RevenueCat webhook secret is not configured",
        )
    received = _extract_header_secret(authorization)
    if not received or not hmac.compare_digest(
        expected_secret.encode(), received.encode()
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature",
        )


def _extract_user_id(event: dict[str, object]) -> str | None:
    value = event.get("app_user_id") or event.get("appUserId") or event.get("user_id")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _extract_event_id(event: dict[str, object]) -> str | None:
    value = event.get("id") or event.get("event_id") or event.get("eventId")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _extract_entitlement_id(event: dict[str, object]) -> str | None:
    single = event.get("entitlement_id")
    if isinstance(single, str) and single.strip():
        return single.strip()

    many = event.get("entitlement_ids")
    if isinstance(many, list):
        entitlement_ids = cast(list[object], many)
        for raw in entitlement_ids:
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
    return None


def _extract_purchase_anchor(event: dict[str, object]) -> datetime | None:
    for key in ("purchased_at_ms", "purchased_at", "purchase_at", "event_timestamp_ms", "event_timestamp"):
        parsed = parse_flexible_datetime(event.get(key))
        if parsed is not None:
            return parsed
    return None


def _extract_expiration(event: dict[str, object]) -> datetime | None:
    for key in ("expiration_at_ms", "expiration_at", "expires_at_ms", "expires_at"):
        parsed = parse_flexible_datetime(event.get(key))
        if parsed is not None:
            return parsed
    return None


@router.post("/webhooks/revenuecat")
async def revenuecat_webhook(
    payload: RevenueCatWebhookPayload,
    authorization: str | None = Header(default=None, alias="Authorization"),
    signature: str | None = Header(default=None, alias="X-RevenueCat-Signature"),
) -> dict[str, object]:
    _verify_webhook_secret(authorization=authorization or signature)

    event = payload.event
    event_type_raw = event.get("type")
    event_type = event_type_raw.upper() if isinstance(event_type_raw, str) else ""
    user_id = _extract_user_id(event)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing RevenueCat app user ID",
        )

    event_id = _extract_event_id(event)
    entitlement_id = _extract_entitlement_id(event)
    purchase_anchor = _extract_purchase_anchor(event) or utc_now()
    expiration_at = _extract_expiration(event)

    if event_type in {"INITIAL_PURCHASE", "UNCANCELLATION"}:
        if expiration_at is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing entitlement expiration in webhook payload",
            )
        credits_status = await ai_credits_service.apply_premium_activation(
            user_id,
            anchor_at=purchase_anchor,
            period_end_at=expiration_at,
            event_id=event_id,
            entitlement_id=entitlement_id,
        )
    elif event_type == "RENEWAL":
        if expiration_at is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing entitlement expiration in webhook payload",
            )
        credits_status = await ai_credits_service.apply_premium_renewal(
            user_id,
            anchor_at=purchase_anchor,
            period_end_at=expiration_at,
            event_id=event_id,
            entitlement_id=entitlement_id,
        )
    elif event_type == "EXPIRATION":
        credits_status = await ai_credits_service.apply_premium_expiration(
            user_id,
            anchor_at=expiration_at or utc_now(),
            event_id=event_id,
        )
    elif event_type == "CANCELLATION":
        credits_status = await ai_credits_service.get_credits_status(user_id)
    else:
        credits_status = await ai_credits_service.get_credits_status(user_id)

    return {
        "ok": True,
        "eventType": event_type,
        "userId": user_id,
        "tier": credits_status.tier,
        "balance": credits_status.balance,
    }
