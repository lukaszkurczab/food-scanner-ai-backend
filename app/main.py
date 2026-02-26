from fastapi import FastAPI

from app.api.router import api_v1_router, root_router
from app.core.config import settings


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        description=settings.DESCRIPTION,
        version=settings.VERSION,
        debug=settings.DEBUG,
    )
    app.include_router(root_router)
    app.include_router(api_v1_router, prefix=settings.API_V1_PREFIX)
    return app


app = create_app()
