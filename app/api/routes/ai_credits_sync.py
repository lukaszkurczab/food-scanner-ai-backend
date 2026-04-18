from datetime import datetime
from typing import cast
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import AuthenticatedUser, get_required_authenticated_user
from app.core.config import settings
from app.core.datetime_utils import add_one_month_clamped, parse_flexible_datetime, utc_now
from app.schemas.ai_credits import AiCreditsResponse
from app.services import ai_credits_service

router = APIRouter()


def _as_object_map(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    raw_map = cast(dict[object, object], value)
    result: dict[str, object] = {}
    for raw_key, raw_item in raw_map.items():
        if isinstance(raw_key, str):
            result[raw_key] = raw_item
    return result


def _extract_active_entitlement(
    subscriber: dict[str, object],
) -> tuple[str, datetime, datetime | None] | None:
    entitlements = _as_object_map(subscriber.get("entitlements"))
    if entitlements is None:
        return None

    now = utc_now()
    for entitlement_id, entitlement_raw in entitlements.items():
        if not entitlement_id.strip():
            continue
        entitlement_map = _as_object_map(entitlement_raw)
        if entitlement_map is None:
            continue

        expires_at = parse_flexible_datetime(
            entitlement_map.get("expires_date") or entitlement_map.get("expires_date_ms")
        )
        if expires_at is not None and expires_at <= now:
            continue

        anchor_at = (
            parse_flexible_datetime(
                entitlement_map.get("purchase_date")
                or entitlement_map.get("purchase_date_ms")
            )
            or parse_flexible_datetime(
                entitlement_map.get("original_purchase_date")
                or entitlement_map.get("original_purchase_date_ms")
            )
            or now
        )

        return entitlement_id.strip(), anchor_at, expires_at

    return None


def _build_sync_event_id(
    *,
    user_id: str,
    entitlement_id: str,
    anchor_at: datetime,
    period_end_at: datetime | None,
) -> str:
    anchor_ts = int(anchor_at.timestamp())
    end_ts = int(period_end_at.timestamp()) if period_end_at is not None else 0
    return f"sync:{user_id}:{entitlement_id}:{anchor_ts}:{end_ts}"


async def _fetch_revenuecat_subscriber(user_id: str) -> dict[str, object]:
    api_key = settings.REVENUECAT_API_KEY.strip()
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="RevenueCat API key is not configured",
        )

    safe_user_id = quote(user_id, safe="")
    url = f"https://api.revenuecat.com/v1/subscribers/{safe_user_id}"
    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="RevenueCat sync unavailable",
        ) from exc

    if response.status_code == status.HTTP_404_NOT_FOUND:
        return {}
    if response.status_code >= status.HTTP_400_BAD_REQUEST:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="RevenueCat sync failed",
        )

    try:
        payload_raw = response.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Invalid RevenueCat response",
        ) from exc
    payload = _as_object_map(payload_raw)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Invalid RevenueCat response",
        )
    return payload


@router.post("/ai/credits/sync-tier", response_model=AiCreditsResponse)
async def sync_ai_credits_tier(
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> AiCreditsResponse:
    user_id = current_user.uid
    revenuecat_payload = await _fetch_revenuecat_subscriber(user_id)
    subscriber_data = _as_object_map(revenuecat_payload.get("subscriber")) or {}

    current_status = await ai_credits_service.get_credits_status(user_id)
    active_entitlement = _extract_active_entitlement(subscriber_data)
    if active_entitlement is None:
        if current_status.tier == "premium":
            status_after_sync = await ai_credits_service.apply_premium_expiration(
                user_id,
                anchor_at=utc_now(),
                event_id=f"sync-expiration:{user_id}:{int(current_status.periodEndAt.timestamp())}",
            )
        else:
            status_after_sync = current_status
    else:
        entitlement_id, anchor_at, period_end_at = active_entitlement
        resolved_period_end_at = period_end_at or add_one_month_clamped(anchor_at)
        status_after_sync = await ai_credits_service.apply_premium_activation(
            user_id,
            anchor_at=anchor_at,
            period_end_at=resolved_period_end_at,
            event_id=_build_sync_event_id(
                user_id=user_id,
                entitlement_id=entitlement_id,
                anchor_at=anchor_at,
                period_end_at=resolved_period_end_at,
            ),
            entitlement_id=entitlement_id,
        )

    return AiCreditsResponse(**status_after_sync.model_dump())
