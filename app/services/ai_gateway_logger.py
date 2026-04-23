"""AI gateway telemetry/logging for legacy v1 analysis flow.

This module intentionally does not write to Firestore. Gateway diagnostics are
sent to structured application logs (observability sink), while a small subset
is emitted as product analytics events.
"""

import logging
from typing import Mapping, TypedDict

from app.services.ai_gateway_service import GatewayResult

OBSERVABILITY_EVENT_NAME = "ai_gateway.decision"
ANALYTICS_EVENT_NAME = "ai_gateway_kpi.decision"
SINK_FALLBACK_EVENT_NAME = "ai_gateway.log_sink_fallback"

_OBSERVABILITY_LOGGER = logging.getLogger("fitaly.ai_gateway.observability")
_ANALYTICS_LOGGER = logging.getLogger("fitaly.ai_gateway.analytics")
_FALLBACK_LOGGER = logging.getLogger(__name__)


class GatewayObservabilityPayload(TypedDict, total=False):
    userId: str
    requestId: str
    threadId: str | None
    actionType: str
    language: str
    tier: str | None
    profile: str | None
    messageLength: int
    decision: str
    reason: str
    score: float
    creditCost: float
    responseTimeMs: float
    executionTimeMs: float
    taskType: str
    hypotheticalDecision: str
    hypotheticalReason: str
    enforced: bool
    model: str | None
    estimatedTokens: int
    actualTokens: int | None
    latencyMs: float | None
    estimatedCost: float
    outcome: str
    failureReason: str
    scopeDecision: str
    retryCount: int
    usedSummary: bool
    truncated: bool
    costCharged: float


class GatewayAnalyticsPayload(TypedDict, total=False):
    eventName: str
    userId: str
    requestId: str
    threadId: str | None
    actionType: str
    decision: str
    reason: str
    outcome: str
    scopeDecision: str
    tier: str | None
    costCharged: float
    creditCost: float
    latencyMs: float | None


def _emit_structured(
    *,
    sink_logger: logging.Logger,
    event_name: str,
    payload: Mapping[str, object],
) -> None:
    try:
        sink_logger.info(
            event_name,
            extra={"context": payload},
        )
    except Exception as exc:  # noqa: BLE001
        fallback_context: dict[str, object] = {
            "sinkEventName": event_name,
            "errorType": exc.__class__.__name__,
        }
        for key in ("userId", "requestId", "threadId", "actionType"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                fallback_context[key] = value
        _FALLBACK_LOGGER.warning(
            SINK_FALLBACK_EVENT_NAME,
            extra={"context": fallback_context},
            exc_info=True,
        )


def _build_observability_payload(
    *,
    user_id: str,
    action_type: str,
    message: str,
    result: GatewayResult,
    language: str,
    response_time_ms: float | None,
    execution_time_ms: float | None,
    profile: str | None,
    tier: str | None,
    credit_cost: float | None,
    thread_id: str | None,
) -> GatewayObservabilityPayload:
    payload: GatewayObservabilityPayload = {
        "userId": user_id,
        "requestId": result["request_id"],
        "threadId": thread_id,
        "actionType": action_type,
        "language": language,
        "tier": tier,
        "profile": profile,
        "messageLength": len(message.strip()),
        "decision": result["decision"],
        "reason": result["reason"],
        "score": result["score"],
        "creditCost": (
            round(credit_cost, 4)
            if credit_cost is not None
            else round(result["credit_cost"], 4)
        ),
    }
    if response_time_ms is not None:
        payload["responseTimeMs"] = round(response_time_ms, 2)
    if execution_time_ms is not None:
        payload["executionTimeMs"] = round(execution_time_ms, 2)
    for source_key, target_key in (
        ("task_type", "taskType"),
        ("hypothetical_decision", "hypotheticalDecision"),
        ("hypothetical_reason", "hypotheticalReason"),
        ("enforced", "enforced"),
        ("model", "model"),
        ("estimated_tokens", "estimatedTokens"),
        ("actual_tokens", "actualTokens"),
        ("latency_ms", "latencyMs"),
        ("estimated_cost", "estimatedCost"),
        ("outcome", "outcome"),
        ("failure_reason", "failureReason"),
        ("scope_decision", "scopeDecision"),
        ("retry_count", "retryCount"),
        ("used_summary", "usedSummary"),
        ("truncated", "truncated"),
        ("cost_charged", "costCharged"),
    ):
        if source_key in result:
            payload[target_key] = result[source_key]
    return payload


def _build_analytics_payload(
    *,
    user_id: str,
    action_type: str,
    result: GatewayResult,
    tier: str | None,
    credit_cost: float | None,
    thread_id: str | None,
) -> GatewayAnalyticsPayload:
    payload: GatewayAnalyticsPayload = {
        "eventName": ANALYTICS_EVENT_NAME,
        "userId": user_id,
        "requestId": result["request_id"],
        "threadId": thread_id,
        "actionType": action_type,
        "decision": result["decision"],
        "reason": result["reason"],
        "tier": tier,
        "creditCost": (
            round(credit_cost, 4)
            if credit_cost is not None
            else round(result["credit_cost"], 4)
        ),
    }
    if "outcome" in result:
        payload["outcome"] = result["outcome"]
    if "scope_decision" in result:
        payload["scopeDecision"] = result["scope_decision"]
    if "cost_charged" in result:
        payload["costCharged"] = result["cost_charged"]
    if "latency_ms" in result:
        payload["latencyMs"] = result["latency_ms"]
    return payload


def log_gateway_decision(
    user_id: str,
    message: str,
    result: GatewayResult,
    action_type: str,
    language: str = "pl",
    *,
    response_time_ms: float | None = None,
    execution_time_ms: float | None = None,
    profile: str | None = None,
    tier: str | None = None,
    credit_cost: float | None = None,
    thread_id: str | None = None,
) -> None:
    """Emit gateway observability logs and minimal product analytics event."""
    observability_payload = _build_observability_payload(
        user_id=user_id,
        action_type=action_type,
        message=message,
        result=result,
        language=language,
        response_time_ms=response_time_ms,
        execution_time_ms=execution_time_ms,
        profile=profile,
        tier=tier,
        credit_cost=credit_cost,
        thread_id=thread_id,
    )
    analytics_payload = _build_analytics_payload(
        user_id=user_id,
        action_type=action_type,
        result=result,
        tier=tier,
        credit_cost=credit_cost,
        thread_id=thread_id,
    )
    _emit_structured(
        sink_logger=_OBSERVABILITY_LOGGER,
        event_name=OBSERVABILITY_EVENT_NAME,
        payload=observability_payload,
    )
    _emit_structured(
        sink_logger=_ANALYTICS_LOGGER,
        event_name=ANALYTICS_EVENT_NAME,
        payload=analytics_payload,
    )
