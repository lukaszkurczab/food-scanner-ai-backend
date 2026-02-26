from datetime import datetime, timezone

from app.schemas.health import HealthResponse


def build_health_response() -> HealthResponse:
    return HealthResponse(
        status="ok",
        service="caloriai-backend",
        timestamp=datetime.now(timezone.utc),
    )
