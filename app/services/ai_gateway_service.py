"""AI gateway — classifies, decides, and enforces request routing.

The gateway sits between the API route and the upstream AI provider.  It
evaluates every request, decides whether to FORWARD, REJECT, or answer
locally, and logs the decision for observability.

Canonical reject/forward reason codes are defined here as module-level
constants so that backend, mobile, and tests all share a single source
of truth.  Mobile's ``GATEWAY_REJECT_REASONS`` set must stay in sync
with the ``REJECT_REASON_*`` constants below.
"""

from __future__ import annotations

import math
import uuid
from typing import Literal, NotRequired, TypedDict

from app.core.config import settings

Decision = Literal["FORWARD", "REJECT", "LOCAL_ANSWER"]
TaskType = Literal["chat", "photo_meal_analysis", "text_meal_analysis", "other"]
GatewayOutcome = Literal["FORWARDED", "BLOCKED", "UPSTREAM_ERROR"]

# ---------------------------------------------------------------------------
# Canonical reason codes — shared contract with mobile & tests
# ---------------------------------------------------------------------------
# Reject reasons (mobile: GATEWAY_REJECT_REASONS set in useChatHistory.ts)
REJECT_REASON_OFF_TOPIC = "OFF_TOPIC"
REJECT_REASON_TOO_SHORT = "TOO_SHORT"

# Forward / pass-through reasons
FORWARD_REASON_PASS_THROUGH = "PASS_THROUGH"
FORWARD_REASON_GATEWAY_DISABLED = "GATEWAY_DISABLED"

# Hypothetical-only reasons (not enforced, logged for analytics)
HYPOTHESIS_TRIVIAL_GREETING = "TRIVIAL_GREETING"


class GatewayResult(TypedDict):
    decision: Decision
    reason: str
    score: float
    credit_cost: float
    request_id: str
    action_type: str
    task_type: TaskType
    hypothetical_decision: NotRequired[Decision]
    hypothetical_reason: NotRequired[str]
    enforced: bool
    model: str | None
    estimated_tokens: int
    actual_tokens: int | None
    latency_ms: float | None
    estimated_cost: float
    outcome: NotRequired[GatewayOutcome]
    failure_reason: NotRequired[str]


def classify_task_type(action_type: str) -> TaskType:
    normalized = action_type.strip().lower().replace("-", "_")
    if normalized == "chat":
        return "chat"
    if "photo" in normalized:
        return "photo_meal_analysis"
    if "text" in normalized and "meal" in normalized:
        return "text_meal_analysis"
    return "other"


def estimate_tokens(message: str, task_type: TaskType) -> int:
    if task_type == "photo_meal_analysis":
        return 800
    normalized = message.strip()
    if not normalized:
        return 1
    return max(1, math.ceil(len(normalized) / 4))


def resolve_gateway_model(task_type: TaskType) -> str | None:
    if task_type == "photo_meal_analysis":
        return "gpt-4o"
    if task_type in {"chat", "text_meal_analysis"}:
        return "gpt-4o-mini"
    return None


def estimate_cost(task_type: TaskType, decision: Decision) -> float:
    if decision == "REJECT":
        return round(settings.AI_REJECT_COST, 4)
    if decision == "LOCAL_ANSWER":
        return round(settings.AI_LOCAL_COST, 4)

    if task_type == "photo_meal_analysis":
        return float(settings.AI_CREDIT_COST_PHOTO)
    if task_type == "text_meal_analysis":
        return float(settings.AI_CREDIT_COST_TEXT_MEAL)
    return float(settings.AI_CREDIT_COST_CHAT)


def build_gateway_result(
    *,
    action_type: str,
    message: str,
    decision: Decision,
    reason: str,
    score: float = 1.0,
    request_id: str | None = None,
    hypothetical_decision: Decision | None = None,
    hypothetical_reason: str | None = None,
    enforced: bool = True,
    actual_tokens: int | None = None,
    latency_ms: float | None = None,
    outcome: GatewayOutcome | None = None,
    failure_reason: str | None = None,
) -> GatewayResult:
    task_type = classify_task_type(action_type)
    estimated_cost = estimate_cost(task_type, decision)
    result: GatewayResult = {
        "decision": decision,
        "reason": reason,
        "score": score,
        "credit_cost": estimated_cost,
        "request_id": request_id or uuid.uuid4().hex,
        "action_type": action_type,
        "task_type": task_type,
        "enforced": enforced,
        "model": resolve_gateway_model(task_type),
        "estimated_tokens": estimate_tokens(message, task_type),
        "actual_tokens": actual_tokens,
        "latency_ms": round(latency_ms, 2) if latency_ms is not None else None,
        "estimated_cost": estimated_cost,
    }
    if hypothetical_decision is not None:
        result["hypothetical_decision"] = hypothetical_decision
    if hypothetical_reason is not None:
        result["hypothetical_reason"] = hypothetical_reason
    if outcome is not None:
        result["outcome"] = outcome
    if failure_reason is not None:
        result["failure_reason"] = failure_reason
    return result


def _classify_hypothetical_decision(message: str) -> tuple[Decision | None, str | None]:
    normalized = message.strip().lower()
    if not normalized:
        return None, None

    if normalized in {"hej", "hello", "hi", "hey", "czesc", "cześć"}:
        return "LOCAL_ANSWER", HYPOTHESIS_TRIVIAL_GREETING

    off_topic_keywords = (
        "pogoda",
        "weather",
        "bitcoin",
        "mecz",
        "match score",
        "lotto",
        "horoscope",
        "horoskop",
    )
    if any(keyword in normalized for keyword in off_topic_keywords):
        return "REJECT", REJECT_REASON_OFF_TOPIC

    return None, None


def evaluate_request(
    user_id: str,
    action_type: str,
    message: str,
    *,
    language: str = "pl",
    request_id: str | None = None,
) -> GatewayResult:
    """Evaluate whether a request should be forwarded to OpenAI."""
    del user_id, language

    if not settings.AI_GATEWAY_ENABLED:
        return build_gateway_result(
            action_type=action_type,
            message=message,
            decision="FORWARD",
            reason=FORWARD_REASON_GATEWAY_DISABLED,
            request_id=request_id,
        )

    hypothetical_decision, hypothetical_reason = _classify_hypothetical_decision(message)
    if classify_task_type(action_type) == "chat" and hypothetical_decision == "REJECT":
        return build_gateway_result(
            action_type=action_type,
            message=message,
            decision="REJECT",
            reason=hypothetical_reason or REJECT_REASON_OFF_TOPIC,
            request_id=request_id,
            hypothetical_decision=hypothetical_decision,
            hypothetical_reason=hypothetical_reason,
            enforced=True,
        )

    return build_gateway_result(
        action_type=action_type,
        message=message,
        decision="FORWARD",
        reason=FORWARD_REASON_PASS_THROUGH,
        request_id=request_id,
        hypothetical_decision=hypothetical_decision,
        hypothetical_reason=hypothetical_reason,
        enforced=False,
    )
