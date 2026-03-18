"""Service helpers for AI credits with rolling monthly periods."""

from datetime import datetime
import logging
from typing import Literal

from firebase_admin.exceptions import FirebaseError
from google.api_core.exceptions import GoogleAPICallError, RetryError
from google.cloud import firestore

from app.core.config import settings
from app.core.datetime_utils import (
    add_one_month_clamped as _add_one_month_clamped,
    ensure_utc_datetime as _ensure_utc_datetime,
    utc_now as _utc_now,
)
from app.core.exceptions import AiCreditsExhaustedError, FirestoreServiceError
from app.db.firebase import get_firestore
from app.schemas.ai_credits import AiCreditsStatus, CreditCosts

logger = logging.getLogger(__name__)

AI_CREDITS_COLLECTION = "ai_credits"
AI_CREDIT_TRANSACTIONS_COLLECTION = "ai_credit_transactions"
Tier = Literal["free", "premium"]


def _coerce_optional_datetime(value: object) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    return _ensure_utc_datetime(value)


def _coerce_int(value: object, fallback: int) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value))
        except ValueError:
            return fallback
    return fallback


def _coerce_optional_str(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


def _tier_allocation(tier: Tier) -> int:
    if tier == "premium":
        return settings.AI_CREDITS_PREMIUM
    return settings.AI_CREDITS_FREE


def _normalize_tier(value: object) -> Tier:
    if value == "premium":
        return "premium"
    return "free"


def _roll_period_forward(
    *,
    now: datetime,
    period_start_at: datetime,
    period_end_at: datetime,
) -> tuple[datetime, datetime, bool]:
    current_start = period_start_at
    current_end = period_end_at
    did_roll = False

    while now >= current_end:
        did_roll = True
        current_start = current_end
        current_end = _add_one_month_clamped(current_start)

    return current_start, current_end, did_roll


def _normalize_document(
    *,
    user_id: str,
    data: dict[str, object],
    now: datetime,
) -> dict[str, object]:
    tier = _normalize_tier(data.get("tier"))
    allocation = _coerce_int(data.get("allocation"), _tier_allocation(tier))
    if allocation <= 0:
        allocation = _tier_allocation(tier)

    period_start_at = _coerce_optional_datetime(data.get("periodStartAt")) or now
    period_end_at = _coerce_optional_datetime(data.get("periodEndAt")) or _add_one_month_clamped(
        period_start_at
    )
    if period_end_at <= period_start_at:
        period_end_at = _add_one_month_clamped(period_start_at)

    balance = _coerce_int(data.get("balance"), allocation)
    balance = max(0, min(balance, allocation))

    return {
        "userId": user_id,
        "tier": tier,
        "balance": balance,
        "allocation": allocation,
        "periodStartAt": period_start_at,
        "periodEndAt": period_end_at,
        "renewalAnchorSource": _coerce_optional_str(data.get("renewalAnchorSource"))
        or "rolling_monthly",
        "revenueCatEntitlementId": _coerce_optional_str(data.get("revenueCatEntitlementId")),
        "revenueCatExpirationAt": _coerce_optional_datetime(data.get("revenueCatExpirationAt")),
        "lastRevenueCatEventId": _coerce_optional_str(data.get("lastRevenueCatEventId")),
        "createdAt": _coerce_optional_datetime(data.get("createdAt")) or now,
        "updatedAt": _coerce_optional_datetime(data.get("updatedAt")) or now,
    }


def _build_cycle_document(
    *,
    user_id: str,
    tier: Tier,
    anchor_at: datetime,
    period_end_at: datetime | None,
    renewal_anchor_source: str,
    now: datetime,
    created_at: datetime,
    last_revenuecat_event_id: str | None,
    revenuecat_entitlement_id: str | None,
    revenuecat_expiration_at: datetime | None,
) -> dict[str, object]:
    normalized_anchor = _ensure_utc_datetime(anchor_at)
    resolved_period_end_at = (
        _ensure_utc_datetime(period_end_at)
        if period_end_at is not None
        else _add_one_month_clamped(normalized_anchor)
    )
    if resolved_period_end_at <= normalized_anchor:
        resolved_period_end_at = _add_one_month_clamped(normalized_anchor)

    allocation = _tier_allocation(tier)
    return {
        "userId": user_id,
        "tier": tier,
        "balance": allocation,
        "allocation": allocation,
        "periodStartAt": normalized_anchor,
        "periodEndAt": resolved_period_end_at,
        "renewalAnchorSource": renewal_anchor_source,
        "revenueCatEntitlementId": revenuecat_entitlement_id,
        "revenueCatExpirationAt": revenuecat_expiration_at,
        "lastRevenueCatEventId": last_revenuecat_event_id,
        "createdAt": created_at,
        "updatedAt": now,
    }


def _document_for_current_period(
    *,
    user_id: str,
    data: dict[str, object] | None,
    now: datetime,
) -> tuple[dict[str, object], bool]:
    if data is None:
        document = _build_cycle_document(
            user_id=user_id,
            tier="free",
            anchor_at=now,
            period_end_at=None,
            renewal_anchor_source="free_cycle_start",
            now=now,
            created_at=now,
            last_revenuecat_event_id=None,
            revenuecat_entitlement_id=None,
            revenuecat_expiration_at=None,
        )
        return document, True

    document = _normalize_document(user_id=user_id, data=data, now=now)
    period_start_at = document["periodStartAt"]
    period_end_at = document["periodEndAt"]
    if not isinstance(period_start_at, datetime) or not isinstance(period_end_at, datetime):
        raise FirestoreServiceError("Invalid AI credits period data.")

    next_start, next_end, did_roll = _roll_period_forward(
        now=now,
        period_start_at=period_start_at,
        period_end_at=period_end_at,
    )
    if did_roll:
        tier = _normalize_tier(document.get("tier"))
        allocation = _coerce_int(document.get("allocation"), _tier_allocation(tier))
        document["balance"] = allocation
        document["periodStartAt"] = next_start
        document["periodEndAt"] = next_end
        document["updatedAt"] = now

    return document, did_roll


@firestore.transactional
def _refresh_if_period_expired_transaction(
    transaction: firestore.Transaction,
    document_ref: firestore.DocumentReference,
    user_id: str,
    now: datetime,
) -> dict[str, object]:
    snapshot = document_ref.get(transaction=transaction)
    data: dict[str, object] | None = (snapshot.to_dict() or {}) if snapshot.exists else None
    document, should_write = _document_for_current_period(user_id=user_id, data=data, now=now)
    if should_write:
        transaction.set(document_ref, document)
    return document


@firestore.transactional
def _deduct_credits_transaction(
    transaction: firestore.Transaction,
    document_ref: firestore.DocumentReference,
    user_id: str,
    now: datetime,
    cost: int,
) -> tuple[dict[str, object], int, int]:
    snapshot = document_ref.get(transaction=transaction)
    data: dict[str, object] | None = (snapshot.to_dict() or {}) if snapshot.exists else None
    document, _ = _document_for_current_period(user_id=user_id, data=data, now=now)

    previous_balance = _coerce_int(document.get("balance"), 0)
    if previous_balance < cost:
        raise AiCreditsExhaustedError("AI credits exhausted.")

    next_balance = previous_balance - cost
    document["balance"] = next_balance
    document["updatedAt"] = now
    transaction.set(document_ref, document)

    return document, previous_balance, next_balance


@firestore.transactional
def _refund_credits_transaction(
    transaction: firestore.Transaction,
    document_ref: firestore.DocumentReference,
    user_id: str,
    now: datetime,
    cost: int,
) -> tuple[dict[str, object], int, int]:
    snapshot = document_ref.get(transaction=transaction)
    data: dict[str, object] | None = (snapshot.to_dict() or {}) if snapshot.exists else None
    document, _ = _document_for_current_period(user_id=user_id, data=data, now=now)

    previous_balance = _coerce_int(document.get("balance"), 0)
    allocation = _coerce_int(document.get("allocation"), _tier_allocation("free"))
    next_balance = min(previous_balance + cost, allocation)

    document["balance"] = next_balance
    document["updatedAt"] = now
    transaction.set(document_ref, document)

    return document, previous_balance, next_balance


async def get_credits_status(user_id: str) -> AiCreditsStatus:
    return await refresh_if_period_expired(user_id)


async def refresh_if_period_expired(user_id: str) -> AiCreditsStatus:
    client: firestore.Client = get_firestore()
    document_ref = client.collection(AI_CREDITS_COLLECTION).document(user_id)
    transaction = client.transaction()
    now = _utc_now()

    try:
        document = _refresh_if_period_expired_transaction(
            transaction,
            document_ref,
            user_id,
            now,
        )
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception("Failed to refresh AI credits.", extra={"user_id": user_id})
        raise FirestoreServiceError("Failed to refresh AI credits.") from exc

    return _build_status(document)


async def deduct_credits(user_id: str, cost: int, action: str) -> AiCreditsStatus:
    if cost <= 0:
        raise ValueError("Credit cost must be greater than zero.")

    client: firestore.Client = get_firestore()
    document_ref = client.collection(AI_CREDITS_COLLECTION).document(user_id)
    transaction = client.transaction()
    now = _utc_now()

    try:
        document, balance_before, balance_after = _deduct_credits_transaction(
            transaction,
            document_ref,
            user_id,
            now,
            cost,
        )
    except AiCreditsExhaustedError:
        raise
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception("Failed to deduct AI credits.", extra={"user_id": user_id})
        raise FirestoreServiceError("Failed to deduct AI credits.") from exc

    _log_credit_transaction(
        user_id=user_id,
        transaction_type="deduct",
        action=action,
        cost=cost,
        balance_before=balance_before,
        balance_after=balance_after,
        document=document,
    )
    return _build_status(document)


async def refund_credits(user_id: str, cost: int, action: str) -> AiCreditsStatus:
    if cost <= 0:
        raise ValueError("Credit cost must be greater than zero.")

    client: firestore.Client = get_firestore()
    document_ref = client.collection(AI_CREDITS_COLLECTION).document(user_id)
    transaction = client.transaction()
    now = _utc_now()

    try:
        document, balance_before, balance_after = _refund_credits_transaction(
            transaction,
            document_ref,
            user_id,
            now,
            cost,
        )
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception("Failed to refund AI credits.", extra={"user_id": user_id})
        raise FirestoreServiceError("Failed to refund AI credits.") from exc

    _log_credit_transaction(
        user_id=user_id,
        transaction_type="refund",
        action=action,
        cost=cost,
        balance_before=balance_before,
        balance_after=balance_after,
        document=document,
    )
    return _build_status(document)


async def start_free_cycle(user_id: str, anchor_at: datetime) -> AiCreditsStatus:
    return await _start_cycle(
        user_id=user_id,
        tier="free",
        anchor_at=anchor_at,
        period_end_at=None,
        renewal_anchor_source="free_cycle_start",
    )


async def start_premium_cycle(
    user_id: str,
    anchor_at: datetime,
    period_end_at: datetime | None,
) -> AiCreditsStatus:
    return await _start_cycle(
        user_id=user_id,
        tier="premium",
        anchor_at=anchor_at,
        period_end_at=period_end_at,
        renewal_anchor_source="premium_cycle_start",
    )


async def apply_subscription_transition(
    user_id: str,
    target_tier: str,
    anchor_at: datetime,
    period_end_at: datetime | None = None,
) -> AiCreditsStatus:
    if target_tier == "premium":
        return await start_premium_cycle(user_id, anchor_at, period_end_at)
    if target_tier == "free":
        return await start_free_cycle(user_id, anchor_at)
    raise ValueError("target_tier must be either 'free' or 'premium'.")


@firestore.transactional
def _apply_subscription_event_transaction(
    transaction: firestore.Transaction,
    document_ref: firestore.DocumentReference,
    user_id: str,
    now: datetime,
    target_tier: Tier,
    anchor_at: datetime,
    period_end_at: datetime | None,
    renewal_anchor_source: str,
    event_id: str | None,
    entitlement_id: str | None,
) -> tuple[dict[str, object], bool, int]:
    snapshot = document_ref.get(transaction=transaction)
    existing_data: dict[str, object] | None = (snapshot.to_dict() or {}) if snapshot.exists else None
    existing_document: dict[str, object] | None = None
    existing_balance = 0
    created_at = now
    last_event_id: str | None = None

    if existing_data is not None:
        existing_document = _normalize_document(user_id=user_id, data=existing_data, now=now)
        existing_balance = _coerce_int(existing_document.get("balance"), 0)
        created_at = _coerce_optional_datetime(existing_document.get("createdAt")) or now
        last_event_id = _coerce_optional_str(existing_document.get("lastRevenueCatEventId"))
        if event_id is not None and event_id == last_event_id:
            return existing_document, False, existing_balance

    resolved_event_id = event_id or last_event_id
    if target_tier == "premium":
        resolved_entitlement_id = entitlement_id or _coerce_optional_str(
            (existing_document or {}).get("revenueCatEntitlementId")
        )
        resolved_period_end_at = (
            _ensure_utc_datetime(period_end_at)
            if period_end_at is not None
            else _add_one_month_clamped(_ensure_utc_datetime(anchor_at))
        )
        document = _build_cycle_document(
            user_id=user_id,
            tier="premium",
            anchor_at=anchor_at,
            period_end_at=resolved_period_end_at,
            renewal_anchor_source=renewal_anchor_source,
            now=now,
            created_at=created_at,
            last_revenuecat_event_id=resolved_event_id,
            revenuecat_entitlement_id=resolved_entitlement_id,
            revenuecat_expiration_at=resolved_period_end_at,
        )
    else:
        document = _build_cycle_document(
            user_id=user_id,
            tier="free",
            anchor_at=anchor_at,
            period_end_at=None,
            renewal_anchor_source=renewal_anchor_source,
            now=now,
            created_at=created_at,
            last_revenuecat_event_id=resolved_event_id,
            revenuecat_entitlement_id=None,
            revenuecat_expiration_at=None,
        )

    transaction.set(document_ref, document)
    return document, True, existing_balance


async def _apply_subscription_event(
    *,
    user_id: str,
    target_tier: Tier,
    anchor_at: datetime,
    period_end_at: datetime | None,
    renewal_anchor_source: str,
    event_id: str | None,
    entitlement_id: str | None = None,
) -> AiCreditsStatus:
    client: firestore.Client = get_firestore()
    document_ref = client.collection(AI_CREDITS_COLLECTION).document(user_id)
    transaction = client.transaction()
    now = _utc_now()

    try:
        document, applied, existing_balance = _apply_subscription_event_transaction(
            transaction,
            document_ref,
            user_id,
            now,
            target_tier,
            anchor_at,
            period_end_at,
            renewal_anchor_source,
            event_id,
            entitlement_id,
        )
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to apply RevenueCat subscription transition.",
            extra={"user_id": user_id, "target_tier": target_tier},
        )
        raise FirestoreServiceError("Failed to apply subscription transition.") from exc

    if applied:
        _log_credit_transaction(
            user_id=user_id,
            transaction_type="subscription_transition",
            action=renewal_anchor_source,
            cost=0,
            balance_before=existing_balance,
            balance_after=_coerce_int(document.get("balance"), 0),
            document=document,
        )
    return _build_status(document)


async def apply_premium_activation(
    user_id: str,
    anchor_at: datetime,
    period_end_at: datetime,
    *,
    event_id: str | None = None,
    entitlement_id: str | None = None,
) -> AiCreditsStatus:
    return await _apply_subscription_event(
        user_id=user_id,
        target_tier="premium",
        anchor_at=anchor_at,
        period_end_at=period_end_at,
        renewal_anchor_source="premium_activation",
        event_id=event_id,
        entitlement_id=entitlement_id,
    )


async def apply_premium_renewal(
    user_id: str,
    anchor_at: datetime,
    period_end_at: datetime,
    *,
    event_id: str | None = None,
    entitlement_id: str | None = None,
) -> AiCreditsStatus:
    return await _apply_subscription_event(
        user_id=user_id,
        target_tier="premium",
        anchor_at=anchor_at,
        period_end_at=period_end_at,
        renewal_anchor_source="premium_renewal",
        event_id=event_id,
        entitlement_id=entitlement_id,
    )


async def apply_premium_expiration(
    user_id: str,
    anchor_at: datetime,
    *,
    event_id: str | None = None,
) -> AiCreditsStatus:
    return await _apply_subscription_event(
        user_id=user_id,
        target_tier="free",
        anchor_at=anchor_at,
        period_end_at=None,
        renewal_anchor_source="premium_expiration_free_cycle_start",
        event_id=event_id,
        entitlement_id=None,
    )


@firestore.transactional
def _start_cycle_transaction(
    transaction: firestore.Transaction,
    document_ref: firestore.DocumentReference,
    user_id: str,
    now: datetime,
    tier: Tier,
    anchor_at: datetime,
    period_end_at: datetime | None,
    renewal_anchor_source: str,
) -> tuple[dict[str, object], int]:
    snapshot = document_ref.get(transaction=transaction)
    existing_data: dict[str, object] = (
        (snapshot.to_dict() or {}) if snapshot.exists else {}
    )
    existing_balance = _coerce_int(existing_data.get("balance"), 0)
    created_at = _coerce_optional_datetime(existing_data.get("createdAt")) or now
    last_event_id = _coerce_optional_str(existing_data.get("lastRevenueCatEventId"))

    if tier == "premium":
        revenuecat_entitlement_id = _coerce_optional_str(
            existing_data.get("revenueCatEntitlementId")
        )
        resolved_expiration = (
            _ensure_utc_datetime(period_end_at)
            if period_end_at is not None
            else _coerce_optional_datetime(existing_data.get("revenueCatExpirationAt"))
        )
    else:
        revenuecat_entitlement_id = None
        resolved_expiration = None

    document = _build_cycle_document(
        user_id=user_id,
        tier=tier,
        anchor_at=anchor_at,
        period_end_at=period_end_at,
        renewal_anchor_source=renewal_anchor_source,
        now=now,
        created_at=created_at,
        last_revenuecat_event_id=last_event_id,
        revenuecat_entitlement_id=revenuecat_entitlement_id,
        revenuecat_expiration_at=resolved_expiration,
    )

    transaction.set(document_ref, document)
    return document, existing_balance


async def _start_cycle(
    *,
    user_id: str,
    tier: Tier,
    anchor_at: datetime,
    period_end_at: datetime | None,
    renewal_anchor_source: str,
) -> AiCreditsStatus:
    client: firestore.Client = get_firestore()
    document_ref = client.collection(AI_CREDITS_COLLECTION).document(user_id)
    transaction = client.transaction()
    now = _utc_now()

    try:
        document, existing_balance = _start_cycle_transaction(
            transaction,
            document_ref,
            user_id,
            now,
            tier,
            anchor_at,
            period_end_at,
            renewal_anchor_source,
        )
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception("Failed to start AI credits cycle.", extra={"user_id": user_id})
        raise FirestoreServiceError("Failed to start AI credits cycle.") from exc

    _log_credit_transaction(
        user_id=user_id,
        transaction_type="cycle_reset",
        action=renewal_anchor_source,
        cost=0,
        balance_before=existing_balance,
        balance_after=_coerce_int(document.get("balance"), 0),
        document=document,
    )
    return _build_status(document)


def _build_status(document: dict[str, object]) -> AiCreditsStatus:
    now = _utc_now()
    tier = _normalize_tier(document.get("tier"))
    return AiCreditsStatus(
        userId=_coerce_optional_str(document.get("userId")) or "",
        tier=tier,
        balance=_coerce_int(document.get("balance"), 0),
        allocation=_coerce_int(document.get("allocation"), _tier_allocation(tier)),
        periodStartAt=_coerce_optional_datetime(document.get("periodStartAt")) or now,
        periodEndAt=_coerce_optional_datetime(document.get("periodEndAt")) or now,
        renewalAnchorSource=_coerce_optional_str(document.get("renewalAnchorSource")),
        revenueCatEntitlementId=_coerce_optional_str(document.get("revenueCatEntitlementId")),
        revenueCatExpirationAt=_coerce_optional_datetime(document.get("revenueCatExpirationAt")),
        lastRevenueCatEventId=_coerce_optional_str(document.get("lastRevenueCatEventId")),
        costs=CreditCosts(
            chat=settings.AI_CREDIT_COST_CHAT,
            textMeal=settings.AI_CREDIT_COST_TEXT_MEAL,
            photo=settings.AI_CREDIT_COST_PHOTO,
        ),
    )


def _log_credit_transaction(
    *,
    user_id: str,
    transaction_type: str,
    action: str,
    cost: int,
    balance_before: int,
    balance_after: int,
    document: dict[str, object],
) -> None:
    period_start_at = _coerce_optional_datetime(document.get("periodStartAt")) or _utc_now()
    period_end_at = _coerce_optional_datetime(document.get("periodEndAt")) or _utc_now()
    tier = _normalize_tier(document.get("tier"))

    transaction_doc: dict[str, object] = {
        "userId": user_id,
        "type": transaction_type,
        "action": action,
        "cost": cost,
        "balanceBefore": balance_before,
        "balanceAfter": balance_after,
        "tier": tier,
        "periodStartAt": period_start_at,
        "periodEndAt": period_end_at,
        "createdAt": _utc_now(),
    }

    try:
        client: firestore.Client = get_firestore()
        client.collection(AI_CREDIT_TRANSACTIONS_COLLECTION).add(transaction_doc)
    except (FirebaseError, GoogleAPICallError, RetryError):
        logger.exception(
            "Failed to log AI credit transaction.",
            extra={"user_id": user_id, "action": action, "transaction_type": transaction_type},
        )
