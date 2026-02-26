from fastapi import FastAPI

from app.api.router import api_router
from app.core.api_version import (
    CURRENT_API_PREFIX,
    CURRENT_API_VERSION,
    NEXT_API_VERSION,
)
from app.core.config import settings


def create_app() -> FastAPI:
    api_version_note = (
        f"Current API: {CURRENT_API_VERSION} ({CURRENT_API_PREFIX}). "
        f"Breaking changes must be released in {NEXT_API_VERSION}."
    )
    app = FastAPI(
        title=settings.APP_NAME,
        description=f"{settings.DESCRIPTION}\n\n{api_version_note}",
        version=settings.VERSION,
        debug=settings.DEBUG,
    )
    app.include_router(api_router)
    return app


app = create_app()
