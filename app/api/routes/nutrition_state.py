from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import AuthenticatedUser, get_required_authenticated_user
from app.api.http_errors import raise_bad_request
from app.core.exceptions import FirestoreServiceError, StateDisabledError
from app.schemas.nutrition_state import NutritionStateResponse
from app.services.nutrition_state_service import get_nutrition_state

router = APIRouter()


@router.get(
    "/users/me/state",
    response_model=NutritionStateResponse,
    summary="Get canonical user nutrition state",
    description=(
        "Returns the canonical v2 nutrition-state contract for the authenticated "
        "user and day. The payload aggregates targets, consumed and remaining "
        "macros, day quality, habit summary, streak, and AI usage with graceful "
        "degradation for non-core subservices."
    ),
)
async def get_user_nutrition_state_me(
    day: str | None = Query(default=None, min_length=10, max_length=10),
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> NutritionStateResponse:
    try:
        return await get_nutrition_state(current_user.uid, day_key=day)
    except ValueError as exc:
        raise_bad_request(exc)
    except StateDisabledError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Nutrition state is disabled",
        ) from exc
    except FirestoreServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to compute nutrition state",
        ) from exc
