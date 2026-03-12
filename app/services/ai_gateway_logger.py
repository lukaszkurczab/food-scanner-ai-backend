"""Firestore logger for AI gateway decisions."""

from datetime import datetime, timezone
from hashlib import sha256
from typing import TypedDict

from google.cloud import firestore

from app.db.firebase import get_firestore
from app.services.ai_gateway_service import GatewayResult

COLLECTION_NAME = "ai_gateway_logs"


class GatewayLogExtras(TypedDict, total=False):
    responseTimeMs: float
    executionTimeMs: float
    profile: str
    tier: str


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
) -> None:
    """Persist one AI gateway decision as a Firestore document."""
    db: firestore.Client = get_firestore()
    normalized_message = message.strip()
    doc: dict[str, object] = {
        "userId": user_id,
        "timestamp": datetime.now(timezone.utc),
        "actionType": action_type,
        "messageHash": sha256(normalized_message.encode("utf-8")).hexdigest(),
        "decision": result["decision"],
        "reason": result["reason"],
        "score": result["score"],
        "creditCost": credit_cost if credit_cost is not None else result["credit_cost"],
        "language": language,
        "length": len(normalized_message),
    }

    if response_time_ms is not None:
        doc["responseTimeMs"] = round(response_time_ms, 2)
    if execution_time_ms is not None:
        doc["executionTimeMs"] = round(execution_time_ms, 2)
    if profile:
        doc["profile"] = profile
    if tier:
        doc["tier"] = tier

    db.collection(COLLECTION_NAME).add(doc)
