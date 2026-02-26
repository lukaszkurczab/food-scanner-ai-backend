"""Central registry for public API versioning.

Migration plan to v2:
- keep `v1` routes stable for existing clients;
- introduce breaking changes only in `v2`;
- run `v1` and `v2` in parallel until all clients migrate.
"""

from app.core.config import settings

CURRENT_API_VERSION = "v1"
CURRENT_API_PREFIX = settings.API_V1_PREFIX

NEXT_API_VERSION = "v2"
NEXT_API_PREFIX = settings.API_V2_PREFIX
