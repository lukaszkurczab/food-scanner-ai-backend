from fastapi import APIRouter, Depends

from app.api.deps import (
    AuthenticatedUser,
    get_required_authenticated_user,
)
from app.schemas.ai_credits import AiCreditsResponse
from app.services import ai_credits_service

router = APIRouter()


@router.get("/ai/credits", response_model=AiCreditsResponse)
async def get_ai_credits(
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> AiCreditsResponse:
    credits_status = await ai_credits_service.get_credits_status(current_user.uid)
    return AiCreditsResponse(**credits_status.model_dump())
