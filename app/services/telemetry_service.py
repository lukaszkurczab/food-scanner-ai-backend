"""Telemetry ingestion service for v2 batch uploads.

Operator visibility
-------------------
Every ingest call emits structured log lines so that operators can
monitor telemetry health without a dedicated dashboard:

* **INFO  telemetry.ingest.ok**   — batch accepted (counters)
* **WARNING telemetry.ingest.rejected** — one or more events had a
  disallowed name (per-event detail)
* **WARNING telemetry.ingest.rate_limited** — caller exceeded the
  sliding-window rate limit
* **ERROR telemetry.ingest.firestore_error** — Firestore write failed
  (existing behaviour, kept for continuity)
"""

from __future__ import annotations

import json
import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
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
    TelemetryDailySummaryBucket,
    TelemetryDailySummaryResponse,
    TelemetryBatchIngestResponse,
    TelemetryBatchRequest,
    RejectedTelemetryEvent,
    TelemetrySummaryEventCount,
)

if TYPE_CHECKING:
    from app.schemas.telemetry import TelemetryEventInput

logger = logging.getLogger(__name__)

COLLECTION_NAME = "telemetry_events"
MAX_BATCH_PAYLOAD_BYTES = 64 * 1024
RATE_LIMIT_WINDOW_SECONDS = 60.0
RATE_LIMIT_MAX_REQUESTS = 60
_request_buckets: dict[str, deque[float]] = {}
TELEMETRY_RETENTION_DAYS = 30


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
        logger.warning(
            "telemetry.ingest.rate_limited",
            extra={"bucket_key": bucket_key, "window_size": len(bucket)},
        )
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
    user_hash = (
        sha256(context.user_id.encode("utf-8")).hexdigest()
        if context.user_id
        else None
    )
    return {
        "eventId": event.eventId,
        "name": event.name,
        "ts": _serialize_timestamp(event.ts),
        "props": event.props or {},
        "sessionId": request.sessionId,
        "userHash": user_hash,
        "platform": request.app.platform,
        "appVersion": request.app.appVersion,
        "build": request.app.build,
        "locale": request.device.locale,
        "tzOffsetMin": request.device.tzOffsetMin,
        "ingestedAt": _serialize_timestamp(utc_now()),
        "expiresAt": ensure_utc_datetime(utc_now() + timedelta(days=TELEMETRY_RETENTION_DAYS)),
    }


def _build_user_hash(user_id: str) -> str:
    return sha256(user_id.encode("utf-8")).hexdigest()


def count_events_for_user(
    *,
    user_id: str,
    event_name: str,
    start_at: datetime,
    end_at: datetime,
) -> int:
    collection_ref = get_firestore().collection(COLLECTION_NAME)
    query = (
        collection_ref.where("userHash", "==", _build_user_hash(user_id))
        .where("name", "==", event_name)
        .where("ts", ">=", _serialize_timestamp(start_at))
        .where("ts", "<=", _serialize_timestamp(end_at))
    )

    try:
        return sum(1 for _ in query.stream())
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "telemetry.count.firestore_error",
            extra={
                "user_id": user_id,
                "event_name": event_name,
                "start_at": _serialize_timestamp(start_at),
                "end_at": _serialize_timestamp(end_at),
            },
        )
        raise FirestoreServiceError("Failed to count telemetry events.") from exc


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
            logger.warning(
                "telemetry.ingest.rejected",
                extra={
                    "event_id": event.eventId,
                    "event_name": event.name,
                    "reason": "event_not_allowed",
                    "session_id": request.sessionId,
                    "user_id": context.user_id,
                },
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
                "telemetry.ingest.firestore_error",
                extra={"event_id": event.eventId, "event_name": event.name},
            )
            raise FirestoreServiceError("Failed to persist telemetry event.") from exc

    rejected_count = len(rejected_events)
    logger.info(
        "telemetry.ingest.ok",
        extra={
            "session_id": request.sessionId,
            "user_id": context.user_id,
            "platform": request.app.platform,
            "app_version": request.app.appVersion,
            "events_total": len(request.events),
            "accepted": accepted_count,
            "duplicates": duplicate_count,
            "rejected": rejected_count,
        },
    )

    return TelemetryBatchIngestResponse(
        acceptedCount=accepted_count,
        duplicateCount=duplicate_count,
        rejectedCount=rejected_count,
        rejectedEvents=rejected_events,
    )


def get_daily_summary(
    *,
    user_id: str,
    days: int = 7,
    now: datetime | None = None,
) -> TelemetryDailySummaryResponse:
    if not settings.TELEMETRY_ENABLED:
        raise TelemetryDisabledError("Telemetry ingestion is disabled")

    normalized_now = ensure_utc_datetime(now or utc_now())
    start_at = normalized_now - timedelta(days=max(days - 1, 0))
    collection_ref = get_firestore().collection(COLLECTION_NAME)
    query = (
        collection_ref.where("userHash", "==", _build_user_hash(user_id))
        .where("ts", ">=", _serialize_timestamp(start_at))
        .where("ts", "<=", _serialize_timestamp(normalized_now))
    )

    buckets: dict[str, dict[str, int]] = {}
    try:
        snapshots = list(query.stream())
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "telemetry.summary.firestore_error",
            extra={"user_id": user_id, "days": days},
        )
        raise FirestoreServiceError("Failed to read telemetry summary.") from exc

    for snapshot in snapshots:
        payload = dict(snapshot.to_dict() or {})
        day = str(payload.get("ts") or "")[:10]
        event_name = str(payload.get("name") or "").strip()
        if len(day) != 10 or not event_name:
            continue
        day_counts = buckets.setdefault(day, {})
        day_counts[event_name] = day_counts.get(event_name, 0) + 1

    summary_buckets = [
        TelemetryDailySummaryBucket(
            day=day,
            totalEvents=sum(event_counts.values()),
            eventCounts=[
                TelemetrySummaryEventCount(name=name, count=count)
                for name, count in sorted(event_counts.items())
            ],
        )
        for day, event_counts in sorted(buckets.items())
    ]
    return TelemetryDailySummaryResponse(
        generatedAt=_serialize_timestamp(normalized_now),
        days=days,
        buckets=summary_buckets,
    )
