from fastapi import APIRouter

from app.api.routes.health import router as health_router
from app.api.routes.version import router as version_router

root_router = APIRouter()
root_router.include_router(health_router, tags=["health"])

api_v1_router = APIRouter()
api_v1_router.include_router(version_router, tags=["meta"])
