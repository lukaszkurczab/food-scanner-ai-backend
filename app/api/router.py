from fastapi import APIRouter

from app.api.v1.router import router as v1_router
from app.api.v2.router import router as v2_router
from app.core.api_version import CURRENT_API_PREFIX, NEXT_API_PREFIX

api_router = APIRouter()

# Compatibility rule:
# - Existing clients keep using CURRENT_API_PREFIX without behavior changes.
# - Breaking response/path changes must go to NEXT_API_PREFIX.
api_router.include_router(v1_router, prefix=CURRENT_API_PREFIX)

# v2 router is mounted now as an extension point.
# Add new/changed endpoints in app/api/v2/routes/* and include them in v2/router.py.
api_router.include_router(v2_router, prefix=NEXT_API_PREFIX)
