import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import register_exception_handlers
from app.api.middleware.ip_rate_limit import IpRateLimitMiddleware
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


def _is_production_environment() -> bool:
    return settings.ENVIRONMENT == "production"


def _parse_cors_origins() -> list[str]:
    return [
        origin.strip()
        for origin in settings.CORS_ORIGINS.split(",")
        if origin.strip()
    ]


def _has_firebase_credentials_configured() -> bool:
    if settings.GOOGLE_APPLICATION_CREDENTIALS.strip():
        return True
    return bool(
        settings.FIREBASE_CLIENT_EMAIL.strip()
        and settings.FIREBASE_PRIVATE_KEY.strip()
    )


def _validate_production_startup_config(cors_origins: list[str]) -> None:
    if not _is_production_environment():
        return

    if not cors_origins:
        raise RuntimeError(
            "Invalid production configuration: CORS_ORIGINS must contain at least one explicit origin."
        )
    if "*" in cors_origins:
        raise RuntimeError(
            "Invalid production configuration: wildcard CORS ('*') is not allowed."
        )
    if not settings.OPENAI_API_KEY.strip():
        raise RuntimeError(
            "Invalid production configuration: OPENAI_API_KEY must be configured."
        )
    if not settings.FIREBASE_PROJECT_ID.strip():
        raise RuntimeError(
            "Invalid production configuration: FIREBASE_PROJECT_ID must be configured."
        )
    if not _has_firebase_credentials_configured():
        raise RuntimeError(
            "Invalid production configuration: provide GOOGLE_APPLICATION_CREDENTIALS "
            "or FIREBASE_CLIENT_EMAIL + FIREBASE_PRIVATE_KEY."
        )


def _resolve_cors_origins() -> list[str]:
    cors_origins = _parse_cors_origins()
    _validate_production_startup_config(cors_origins)
    if cors_origins:
        return cors_origins
    if not _is_production_environment():
        return ["*"]
    raise RuntimeError(
        f"CORS_ORIGINS must be configured for environment '{settings.ENVIRONMENT}'"
    )


def create_app() -> FastAPI:
    api_version_note = (
        f"Current API: {CURRENT_API_VERSION} ({CURRENT_API_PREFIX}). "
        f"Breaking changes must be released in {NEXT_API_VERSION}."
    )
    cors_origins = _resolve_cors_origins()

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
    app.add_middleware(IpRateLimitMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Accept"],
    )
    register_exception_handlers(app)
    app.include_router(api_router)
    app.include_router(webhooks_router)

    try:
        get_firestore()
    # Intentionally broad: Firebase SDK can raise various internal errors on init.
    except Exception:  # noqa: BLE001
        logger.exception("Failed to initialize Firebase during application startup.")
        if _is_production_environment():
            raise

    return app


app = create_app()
