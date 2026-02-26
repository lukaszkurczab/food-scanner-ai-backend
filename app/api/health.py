from fastapi import APIRouter

from app.schemas.health import HealthResponse
from app.services.health_service import build_health_response

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health_check() -> HealthResponse:
    return build_health_response()
