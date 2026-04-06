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

from collections import deque
import logging
import math
from time import monotonic, time
import uuid
from typing import Literal, NotRequired, TypedDict

from google.cloud import firestore

from app.core.config import settings
from app.core.firestore_constants import RATE_LIMITS_COLLECTION
from app.db.firebase import get_firestore

logger = logging.getLogger(__name__)

Decision = Literal["FORWARD", "REJECT", "LOCAL_ANSWER"]
TaskType = Literal["chat", "photo_meal_analysis", "text_meal_analysis", "other"]
GatewayOutcome = Literal["FORWARDED", "REJECTED", "LOCAL", "UPSTREAM_ERROR"]

# ---------------------------------------------------------------------------
# Canonical reason codes — shared contract with mobile & tests
# ---------------------------------------------------------------------------
# Reject reasons (mobile: GATEWAY_REJECT_REASONS set in useChatHistory.ts)
REJECT_REASON_OFF_TOPIC = "OFF_TOPIC"
REJECT_REASON_TOO_SHORT = "TOO_SHORT"
GUARD_REASON_RATE_LIMITED = "RATE_LIMITED"
GUARD_REASON_MESSAGE_TOO_LONG = "MESSAGE_TOO_LONG"
GUARD_REASON_PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"

# Forward / pass-through reasons
FORWARD_REASON_PASS_THROUGH = "PASS_THROUGH"
FORWARD_REASON_GATEWAY_DISABLED = "GATEWAY_DISABLED"

# Hypothetical-only reasons (not enforced, logged for analytics)
HYPOTHESIS_TRIVIAL_GREETING = "TRIVIAL_GREETING"

RATE_LIMIT_WINDOW_SECONDS = 60.0
RATE_LIMIT_MAX_REQUESTS = 20
MAX_CHAT_MESSAGE_CHARS = 4_000
MAX_TEXT_PAYLOAD_CHARS = 4_000
MAX_PHOTO_PAYLOAD_CHARS = 4_000_000

# In-memory fallback used only by tests (patched via mocker).
_request_buckets: dict[str, deque[float]] = {}


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


def reset_rate_limit_state() -> None:
    """No-op kept for backward compatibility. Tests should mock _consume_rate_limit_slot."""
    _request_buckets.clear()


@firestore.transactional
def _consume_rate_limit_transaction(
    transaction: firestore.Transaction,
    ref: firestore.DocumentReference,
    now: float,
) -> bool:
    """Atomically check and record a rate-limit slot in Firestore.

    Works correctly across multiple Gunicorn workers because the state lives in
    Firestore rather than in a per-process dict.
    """
    window_start = now - RATE_LIMIT_WINDOW_SECONDS
    snapshot = ref.get(transaction=transaction)
    raw: list[object] = (snapshot.to_dict() or {}).get("ts", []) if snapshot.exists else []
    timestamps = [float(t) for t in raw if isinstance(t, (int, float)) and float(t) > window_start]
    if len(timestamps) >= RATE_LIMIT_MAX_REQUESTS:
        return False
    timestamps.append(now)
    transaction.set(ref, {"ts": timestamps})
    return True


async def _consume_rate_limit_slot(user_id: str) -> bool:
    """Distributed rate-limit check backed by Firestore.

    Falls back to allowing the request if Firestore is unreachable so that a
    transient DB issue never silently blocks all AI traffic.
    """
    try:
        client = get_firestore()
        ref = client.collection(RATE_LIMITS_COLLECTION).document(user_id)
        transaction = client.transaction()
        return _consume_rate_limit_transaction(transaction, ref, time())
    except Exception:
        logger.exception("Rate-limit Firestore check failed for user %s — allowing request", user_id)
        return True


def _max_payload_chars(task_type: TaskType) -> int:
    if task_type == "photo_meal_analysis":
        return MAX_PHOTO_PAYLOAD_CHARS
    return MAX_TEXT_PAYLOAD_CHARS


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


async def evaluate_request(
    user_id: str,
    action_type: str,
    message: str,
    *,
    language: str = "pl",
    request_id: str | None = None,
    raw_payload_chars: int | None = None,
) -> GatewayResult:
    """Evaluate whether a request should be forwarded to OpenAI."""
    del language

    task_type = classify_task_type(action_type)
    normalized_message = message.strip()
    payload_chars = raw_payload_chars if raw_payload_chars is not None else len(normalized_message)

    if not settings.AI_GATEWAY_ENABLED:
        return build_gateway_result(
            action_type=action_type,
            message=message,
            decision="FORWARD",
            reason=FORWARD_REASON_GATEWAY_DISABLED,
            request_id=request_id,
            enforced=False,
        )

    if not await _consume_rate_limit_slot(user_id):
        return build_gateway_result(
            action_type=action_type,
            message=message,
            decision="REJECT",
            reason=GUARD_REASON_RATE_LIMITED,
            request_id=request_id,
            enforced=True,
        )

    if task_type == "chat" and len(normalized_message) > MAX_CHAT_MESSAGE_CHARS:
        return build_gateway_result(
            action_type=action_type,
            message=message,
            decision="REJECT",
            reason=GUARD_REASON_MESSAGE_TOO_LONG,
            request_id=request_id,
            enforced=True,
        )

    if payload_chars > _max_payload_chars(task_type):
        return build_gateway_result(
            action_type=action_type,
            message=message,
            decision="REJECT",
            reason=GUARD_REASON_PAYLOAD_TOO_LARGE,
            request_id=request_id,
            enforced=True,
        )

    hypothetical_decision, hypothetical_reason = _classify_hypothetical_decision(message)
    if task_type == "chat" and hypothetical_decision == "REJECT":
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
