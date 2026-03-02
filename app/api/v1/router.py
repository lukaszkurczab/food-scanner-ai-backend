from fastapi import APIRouter

from app.api.routes.health import router as health_router
from app.api.routes.version import router as version_router
from app.api.routes.ai_usage import router as ai_usage_router
from app.api.routes.ai import router as ai_router

router = APIRouter()
router.include_router(health_router, tags=["health"])
router.include_router(version_router, tags=["meta"])
router.include_router(ai_usage_router, tags=["ai"], prefix="")
router.include_router(ai_router, tags=["ai"], prefix="")
