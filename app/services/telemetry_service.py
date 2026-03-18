"""Telemetry ingestion service for v2 batch uploads."""

from __future__ import annotations

import json
import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from time import monotonic
from typing import TYPE_CHECKING, Any, cast

from firebase_admin.exceptions import FirebaseError
from google.api_core.exceptions import AlreadyExists, GoogleAPICallError, RetryError
from google.cloud import firestore

from app.core.datetime_utils import ensure_utc_datetime, utc_now
from app.core.config import settings
from app.core.exceptions import (
    FirestoreServiceError,
    TelemetryDisabledError,
    TelemetryPayloadTooLargeError,
    TelemetryRateLimitError,
)
from app.db.firebase import get_firestore
from app.schemas.telemetry import (
    ALLOWED_TELEMETRY_EVENT_NAMES,
    TelemetryBatchIngestResponse,
    TelemetryBatchRequest,
    RejectedTelemetryEvent,
)

if TYPE_CHECKING:
    from app.schemas.telemetry import TelemetryEventInput

logger = logging.getLogger(__name__)

COLLECTION_NAME = "telemetry_events"
MAX_BATCH_PAYLOAD_BYTES = 64 * 1024
RATE_LIMIT_WINDOW_SECONDS = 60.0
RATE_LIMIT_MAX_REQUESTS = 60
_request_buckets: dict[str, deque[float]] = {}


@dataclass(frozen=True)
class TelemetryRequestContext:
    client_host: str
    user_id: str | None


def reset_rate_limit_state() -> None:
    _request_buckets.clear()


def build_bucket_key(context: TelemetryRequestContext) -> str:
    client_host = context.client_host.strip() or "anonymous"
    if context.user_id:
        return f"user:{context.user_id}"
    return f"ip:{client_host}"


def _check_rate_limit(bucket_key: str) -> None:
    now = monotonic()
    bucket = _request_buckets.setdefault(bucket_key, deque())
    threshold = now - RATE_LIMIT_WINDOW_SECONDS

    while bucket and bucket[0] <= threshold:
        bucket.popleft()

    if len(bucket) >= RATE_LIMIT_MAX_REQUESTS:
        raise TelemetryRateLimitError("Too many telemetry requests")

    bucket.append(now)


def _validate_payload_size(request: TelemetryBatchRequest) -> None:
    payload = request.model_dump(mode="json", exclude_none=True)
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(serialized.encode("utf-8")) > MAX_BATCH_PAYLOAD_BYTES:
        raise TelemetryPayloadTooLargeError("Telemetry payload is too large")


def _serialize_timestamp(value: datetime) -> str:
    normalized = ensure_utc_datetime(value)
    return normalized.isoformat().replace("+00:00", "Z")


def _build_document(
    request: TelemetryBatchRequest,
    event: "TelemetryEventInput",
    context: TelemetryRequestContext,
) -> dict[str, object]:
    return {
        "eventId": event.eventId,
        "name": event.name,
        "ts": _serialize_timestamp(event.ts),
        "props": event.props or {},
        "sessionId": request.sessionId,
        "userId": context.user_id,
        "platform": request.app.platform,
        "appVersion": request.app.appVersion,
        "build": request.app.build,
        "locale": request.device.locale,
        "tzOffsetMin": request.device.tzOffsetMin,
        "ingestedAt": _serialize_timestamp(utc_now()),
    }


def ingest_batch(
    request: TelemetryBatchRequest,
    context: TelemetryRequestContext,
) -> TelemetryBatchIngestResponse:
    if not settings.TELEMETRY_ENABLED:
        raise TelemetryDisabledError("Telemetry ingestion is disabled")

    _check_rate_limit(build_bucket_key(context))
    _validate_payload_size(request)

    collection_ref: firestore.CollectionReference | None = None
    accepted_count = 0
    duplicate_count = 0
    rejected_events: list[RejectedTelemetryEvent] = []

    for event in request.events:
        if event.name not in ALLOWED_TELEMETRY_EVENT_NAMES:
            rejected_events.append(
                RejectedTelemetryEvent(
                    eventId=event.eventId,
                    name=event.name,
                    reason="event_not_allowed",
                )
            )
            continue

        try:
            if collection_ref is None:
                db: firestore.Client = get_firestore()
                collection_ref = db.collection(COLLECTION_NAME)
            document_ref = cast(Any, collection_ref.document(event.eventId))
            document_ref.create(_build_document(request, event, context))
            accepted_count += 1
        except AlreadyExists:
            duplicate_count += 1
        except (FirebaseError, GoogleAPICallError, RetryError) as exc:
            logger.exception(
                "Failed to ingest telemetry batch.",
                extra={"event_id": event.eventId, "event_name": event.name},
            )
            raise FirestoreServiceError("Failed to persist telemetry event.") from exc

    return TelemetryBatchIngestResponse(
        acceptedCount=accepted_count,
        duplicateCount=duplicate_count,
        rejectedCount=len(rejected_events),
        rejectedEvents=rejected_events,
    )
