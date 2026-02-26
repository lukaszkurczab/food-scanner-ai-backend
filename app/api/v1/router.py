from fastapi import APIRouter

from app.api.routes.health import router as health_router
from app.api.routes.version import router as version_router

router = APIRouter()
router.include_router(health_router, tags=["health"])
router.include_router(version_router, tags=["meta"])
