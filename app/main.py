import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import register_exception_handlers
from app.api.middleware import request_logging
from app.api.routes.webhooks import router as webhooks_router
from app.api.router import api_router
from app.core.api_version import (
    CURRENT_API_PREFIX,
    CURRENT_API_VERSION,
    NEXT_API_VERSION,
)
from app.core.config import settings
from app.core.monitoring import init_sentry
from app.db.firebase import get_firestore

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    api_version_note = (
        f"Current API: {CURRENT_API_VERSION} ({CURRENT_API_PREFIX}). "
        f"Breaking changes must be released in {NEXT_API_VERSION}."
    )
    cors_origins = [origin.strip() for origin in settings.CORS_ORIGINS.split(",") if origin.strip()]
    if not cors_origins:
        cors_origins = ["*"]

    app = FastAPI(
        title=settings.APP_NAME,
        description=(
            f"{settings.DESCRIPTION}\n\n"
            "CORS is configured for allowed client origins and Firebase is "
            "initialized during application startup.\n\n"
            f"{api_version_note}"
        ),
        version=settings.VERSION,
        debug=settings.DEBUG,
    )
    init_sentry()
    app.add_middleware(request_logging.RequestLoggingMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_exception_handlers(app)
    app.include_router(api_router)
    app.include_router(webhooks_router)

    try:
        get_firestore()
    except Exception:
        logger.exception("Failed to initialize Firebase during application startup.")

    return app


app = create_app()
