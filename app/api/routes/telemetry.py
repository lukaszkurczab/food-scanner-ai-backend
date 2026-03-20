from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.deps import (
    AuthenticatedUser,
    get_optional_authenticated_user,
    get_required_authenticated_user,
)
from app.core.exceptions import (
    FirestoreServiceError,
    TelemetryDisabledError,
    TelemetryPayloadTooLargeError,
    TelemetryRateLimitError,
)
from app.schemas.telemetry import (
    SmartReminderRolloutSummaryResponse,
    TelemetryBatchIngestResponse,
    TelemetryBatchRequest,
    TelemetryDailySummaryResponse,
)
from app.services.telemetry_service import (
    TelemetryRequestContext,
    get_daily_summary,
    get_smart_reminder_summary,
    ingest_batch,
)

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


@router.get(
    "/telemetry/events/summary/daily",
    response_model=TelemetryDailySummaryResponse,
    summary="Read daily telemetry summary for the current user",
    description=(
        "Returns a per-day summary of the authenticated user's telemetry "
        "events grouped by canonical event name. This is a lightweight "
        "consumption layer for product and rollout validation."
    ),
)
def get_telemetry_daily_summary(
    days: int = Query(7, ge=1, le=30),
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> TelemetryDailySummaryResponse:
    try:
        return get_daily_summary(user_id=current_user.uid, days=days)
    except TelemetryDisabledError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Telemetry ingestion is disabled",
        ) from exc
    except FirestoreServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to read telemetry summary",
        ) from exc


@router.get(
    "/telemetry/smart-reminders/summary",
    response_model=SmartReminderRolloutSummaryResponse,
    summary="Smart Reminders rollout summary",
    description=(
        "Returns an aggregated view of smart-reminder telemetry for the "
        "authenticated user: outcome counts (send/suppress/noop/fail), "
        "suppression and noop reason distribution, reminder kind breakdown, "
        "and a per-day timeline.  Designed for rollout validation — not "
        "a full BI surface."
    ),
)
def get_smart_reminder_rollout_summary(
    days: int = Query(7, ge=1, le=30),
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> SmartReminderRolloutSummaryResponse:
    try:
        return get_smart_reminder_summary(user_id=current_user.uid, days=days)
    except TelemetryDisabledError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Telemetry ingestion is disabled",
        ) from exc
    except FirestoreServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to read smart reminder summary",
        ) from exc
