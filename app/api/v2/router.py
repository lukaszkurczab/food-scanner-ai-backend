from fastapi import APIRouter

from app.core.config import settings

router = APIRouter()

if settings.TELEMETRY_ENABLED:
    from app.api.routes.telemetry import router as telemetry_router
    router.include_router(telemetry_router, tags=["telemetry"], prefix="")

if settings.HABITS_ENABLED:
    from app.api.routes.habits import router as habits_router
    router.include_router(habits_router, tags=["habits"], prefix="")

if settings.STATE_ENABLED:
    from app.api.routes.nutrition_state import router as nutrition_state_router
    from app.api.routes.coach import router as coach_router
    router.include_router(nutrition_state_router, tags=["nutrition-state"], prefix="")
    router.include_router(coach_router, tags=["coach"], prefix="")

if settings.SMART_REMINDERS_ENABLED:
    from app.api.routes.reminders import router as reminders_router
    router.include_router(reminders_router, tags=["reminders"], prefix="")

if settings.WEEKLY_REPORTS_ENABLED:
    from app.api.routes.weekly_reports import router as weekly_reports_router
    router.include_router(weekly_reports_router, tags=["weekly-reports"], prefix="")
