from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import (
    AuthenticatedUser,
    get_optional_authenticated_user,
    get_required_authenticated_user,
)
from app.core.exceptions import FirestoreServiceError
from app.schemas.username import (
    UsernameAvailabilityResponse,
    UsernameClaimRequest,
    UsernameClaimResponse,
)
from app.services import username_service
from app.services.username_service import (
    UsernameUnavailableError,
    UsernameValidationError,
)

router = APIRouter()


@router.get("/usernames/availability", response_model=UsernameAvailabilityResponse)
async def get_username_availability(
    username: str,
    current_user: AuthenticatedUser | None = Depends(get_optional_authenticated_user),
) -> UsernameAvailabilityResponse:
    try:
        normalized_username, available = await username_service.is_username_available(
            username,
            current_user_id=current_user.uid if current_user else None,
        )
    except FirestoreServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve username availability",
        ) from exc

    return UsernameAvailabilityResponse(
        username=normalized_username,
        available=available,
    )


async def _claim_username_for_user(
    *,
    user_id: str,
    request: UsernameClaimRequest,
) -> UsernameClaimResponse:
    try:
        normalized_username = await username_service.claim_username(
            user_id,
            request.username,
        )
    except UsernameValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except UsernameUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username unavailable",
        ) from exc
    except FirestoreServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error",
        ) from exc

    return UsernameClaimResponse(
        username=normalized_username,
        updated=True,
    )


@router.post("/users/me/username", response_model=UsernameClaimResponse)
async def claim_username_me(
    request: UsernameClaimRequest,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> UsernameClaimResponse:
    return await _claim_username_for_user(user_id=current_user.uid, request=request)
