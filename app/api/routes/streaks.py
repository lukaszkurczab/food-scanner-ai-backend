from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import AuthenticatedUser, get_required_authenticated_user
from app.core.exceptions import FirestoreServiceError
from app.schemas.streak import (
    StreakRecalculateRequest,
    StreakResponse,
    StreakWriteRequest,
)
from app.services import streak_service
from app.services.streak_service import (
    StreakState,
    StreakValidationError,
    _streak_current,
    _streak_last_date,
)

router = APIRouter()


def _build_response(
    *,
    streak: StreakState,
    awarded_badge_ids: list[str] | None = None,
) -> StreakResponse:
    return StreakResponse(
        current=_streak_current(streak),
        lastDate=_streak_last_date(streak),
        awardedBadgeIds=awarded_badge_ids or [],
    )


async def _get_streak_for_user(*, user_id: str) -> StreakResponse:
    try:
        streak = await streak_service.get_streak(user_id)
    except FirestoreServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error",
        ) from exc

    return _build_response(streak=streak)


async def _ensure_streak_for_user(
    *,
    user_id: str,
    request: StreakWriteRequest,
) -> StreakResponse:
    try:
        streak, awarded_badge_ids = await streak_service.ensure_streak(
            user_id,
            request.dayKey,
        )
    except StreakValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except FirestoreServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error",
        ) from exc

    return _build_response(
        streak=streak,
        awarded_badge_ids=awarded_badge_ids,
    )


async def _reset_streak_if_missed_for_user(
    *,
    user_id: str,
    request: StreakWriteRequest,
) -> StreakResponse:
    try:
        streak, awarded_badge_ids = await streak_service.reset_streak_if_missed(
            user_id,
            request.dayKey,
        )
    except StreakValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except FirestoreServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error",
        ) from exc

    return _build_response(
        streak=streak,
        awarded_badge_ids=awarded_badge_ids,
    )


async def _recalculate_streak_for_user(
    *,
    user_id: str,
    request: StreakRecalculateRequest,
) -> StreakResponse:
    try:
        streak, awarded_badge_ids = await streak_service.recalculate_streak(
            user_id=user_id,
            day_key=request.dayKey,
            todays_kcal=request.todaysKcal,
            target_kcal=request.targetKcal,
            threshold_pct=request.thresholdPct,
        )
    except StreakValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except FirestoreServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error",
        ) from exc

    return _build_response(
        streak=streak,
        awarded_badge_ids=awarded_badge_ids,
    )


@router.get("/users/me/streak", response_model=StreakResponse)
async def get_streak_me(
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> StreakResponse:
    return await _get_streak_for_user(user_id=current_user.uid)


@router.post("/users/me/streak/ensure", response_model=StreakResponse)
async def ensure_streak_me(
    request: StreakWriteRequest,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> StreakResponse:
    return await _ensure_streak_for_user(user_id=current_user.uid, request=request)


@router.post("/users/me/streak/reset-if-missed", response_model=StreakResponse)
async def reset_streak_if_missed_me(
    request: StreakWriteRequest,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> StreakResponse:
    return await _reset_streak_if_missed_for_user(user_id=current_user.uid, request=request)


@router.post("/users/me/streak/recalculate", response_model=StreakResponse)
async def recalculate_streak_me(
    request: StreakRecalculateRequest,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> StreakResponse:
    return await _recalculate_streak_for_user(user_id=current_user.uid, request=request)
