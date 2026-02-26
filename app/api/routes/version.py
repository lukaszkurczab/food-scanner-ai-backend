from fastapi import APIRouter

from app.core.config import settings
from app.schemas.version import VersionResponse
from app.services.version_service import build_version_response

router = APIRouter()


@router.get("/version", response_model=VersionResponse)
def get_api_version() -> VersionResponse:
    return build_version_response(settings.VERSION)
