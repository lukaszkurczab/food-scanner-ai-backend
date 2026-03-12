from fastapi import APIRouter

from app.api.routes.health import router as health_router
from app.api.routes.logs import router as logs_router
from app.api.routes.version import router as version_router
from app.api.routes.ai import router as ai_router
from app.api.routes.ai_credits import router as ai_credits_router
from app.api.routes.ai_credits_sync import router as ai_credits_sync_router
from app.api.routes.streaks import router as streaks_router
from app.api.routes.users import router as users_router
from app.api.routes.usernames import router as usernames_router
from app.api.routes.badges import router as badges_router
from app.api.routes.notifications import router as notifications_router
from app.api.routes.chat_threads import router as chat_threads_router
from app.api.routes.meals import router as meals_router
from app.api.routes.my_meals import router as my_meals_router
from app.api.routes.feedback import router as feedback_router

router = APIRouter()
router.include_router(health_router, tags=["health"])
router.include_router(logs_router, tags=["logs"], prefix="")
router.include_router(version_router, tags=["meta"])
router.include_router(ai_router, tags=["ai"], prefix="")
router.include_router(ai_credits_router, tags=["ai"], prefix="")
router.include_router(ai_credits_sync_router, tags=["ai"], prefix="")
router.include_router(usernames_router, tags=["users"], prefix="")
router.include_router(users_router, tags=["users"], prefix="")
router.include_router(streaks_router, tags=["users"], prefix="")
router.include_router(badges_router, tags=["users"], prefix="")
router.include_router(notifications_router, tags=["users"], prefix="")
router.include_router(chat_threads_router, tags=["users"], prefix="")
router.include_router(meals_router, tags=["users"], prefix="")
router.include_router(my_meals_router, tags=["users"], prefix="")
router.include_router(feedback_router, tags=["users"], prefix="")
