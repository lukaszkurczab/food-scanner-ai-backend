from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import AuthenticatedUser, get_required_authenticated_user
from app.core.exceptions import FirestoreServiceError
from app.schemas.badge import (
    BadgeListResponse,
    PremiumBadgeReconcileRequest,
    PremiumBadgeReconcileResponse,
)
from app.services import badge_service

router = APIRouter()


async def _list_badges_for_user(*, user_id: str) -> BadgeListResponse:
    try:
        items = await badge_service.list_badges(user_id)
    except FirestoreServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error",
        ) from exc

    return BadgeListResponse(items=items)


async def _reconcile_premium_badges_for_user(
    *,
    user_id: str,
    request: PremiumBadgeReconcileRequest,
) -> PremiumBadgeReconcileResponse:
    try:
        awarded_badge_ids, has_premium_badge = (
            await badge_service.reconcile_premium_badges(
                user_id,
                is_premium=request.isPremium,
                now_ms=request.nowMs,
            )
        )
    except FirestoreServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error",
        ) from exc

    return PremiumBadgeReconcileResponse(
        awardedBadgeIds=awarded_badge_ids,
        hasPremiumBadge=has_premium_badge,
        updated=True,
    )


@router.post(
    "/users/me/badges/premium/reconcile",
    response_model=PremiumBadgeReconcileResponse,
)
async def reconcile_premium_badges_me(
    request: PremiumBadgeReconcileRequest,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> PremiumBadgeReconcileResponse:
    return await _reconcile_premium_badges_for_user(
        user_id=current_user.uid,
        request=request,
    )


@router.get("/users/me/badges", response_model=BadgeListResponse)
async def list_badges_me(
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> BadgeListResponse:
    return await _list_badges_for_user(user_id=current_user.uid)
