from fastapi import APIRouter

from app.api.routes.coach import router as coach_router
from app.api.routes.habits import router as habits_router
from app.api.routes.nutrition_state import router as nutrition_state_router
from app.api.routes.telemetry import router as telemetry_router

router = APIRouter()
router.include_router(telemetry_router, tags=["telemetry"], prefix="")
router.include_router(habits_router, tags=["habits"], prefix="")
router.include_router(nutrition_state_router, tags=["nutrition-state"], prefix="")
router.include_router(coach_router, tags=["coach"], prefix="")
