from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.deps import AuthenticatedUser, get_optional_authenticated_user
from app.core.exceptions import (
    FirestoreServiceError,
    TelemetryDisabledError,
    TelemetryPayloadTooLargeError,
    TelemetryRateLimitError,
)
from app.schemas.telemetry import TelemetryBatchIngestResponse, TelemetryBatchRequest
from app.services.telemetry_service import TelemetryRequestContext, ingest_batch

router = APIRouter()


@router.post(
    "/telemetry/events/batch",
    response_model=TelemetryBatchIngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ingest telemetry batch events",
    description=(
        "Accepts a batch of mobile or backend telemetry events under the v2 "
        "foundation contract. Applies whitelist validation, payload guards, "
        "idempotent persistence by eventId, and basic rate limiting. "
        "This endpoint is ingestion-only and does not perform aggregation."
    ),
)
def ingest_telemetry_batch(
    http_request: Request,
    request: TelemetryBatchRequest,
    current_user: AuthenticatedUser | None = Depends(get_optional_authenticated_user),
) -> TelemetryBatchIngestResponse:
    context = TelemetryRequestContext(
        client_host=(http_request.client.host if http_request.client else "") or "anonymous",
        user_id=current_user.uid if current_user is not None else None,
    )

    try:
        return ingest_batch(request=request, context=context)
    except TelemetryDisabledError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Telemetry ingestion is disabled",
        ) from exc
    except TelemetryPayloadTooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Telemetry payload is too large",
        ) from exc
    except TelemetryRateLimitError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many telemetry requests",
        ) from exc
    except FirestoreServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to ingest telemetry batch",
        ) from exc
