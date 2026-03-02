from fastapi import APIRouter, HTTPException, status

from app.core.exceptions import FirestoreServiceError
from app.schemas.ai_usage import AiUsageResponse
from app.services import ai_usage_service

router = APIRouter()


@router.get("/ai/usage", response_model=AiUsageResponse)
async def get_ai_usage(userId: str) -> AiUsageResponse:
    try:
        usage_count, daily_limit, date_key = await ai_usage_service.get_usage(userId)
    except FirestoreServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve usage",
        ) from exc

    remaining = daily_limit - usage_count
    return AiUsageResponse(
        userId=userId,
        dateKey=date_key,
        usageCount=usage_count,
        dailyLimit=daily_limit,
        remaining=remaining,
    )
