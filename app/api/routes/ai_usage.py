from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import (
    AuthenticatedUser,
    get_required_authenticated_user,
)
from app.core.exceptions import FirestoreServiceError
from app.schemas.ai_usage import AiUsageResponse
from app.services import ai_usage_service

router = APIRouter()


@router.get("/ai/usage", response_model=AiUsageResponse)
async def get_ai_usage(
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> AiUsageResponse:
    user_id = current_user.uid
    try:
        usage_count, daily_limit, date_key = await ai_usage_service.get_usage(user_id)
    except FirestoreServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve usage",
        ) from exc

    remaining = round(daily_limit - usage_count, 4)
    return AiUsageResponse(
        dateKey=date_key,
        usageCount=usage_count,
        dailyLimit=daily_limit,
        remaining=remaining,
    )
