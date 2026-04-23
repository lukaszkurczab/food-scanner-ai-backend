#!/usr/bin/env python3
"""Backfill Firestore canonical v2 schema for billing + meals.

Migration scope:
1) ai_credits/{uid} -> users/{uid}/billing/main/aiCredits/current
2) ai_credit_transactions/{txId} -> users/{uid}/billing/main/aiCreditTransactions/{txId}
3) users/{uid}/meals/{mealId} canonicalization:
   - id = document id
   - timestamp -> loggedAt
   - remove redundant legacy fields
   - remove photoLocalPath
   - build canonical imageRef

Safety features:
- dry-run mode
- idempotent writes
- retry policy
- read/write batching
- resumable state file
- structured final report
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sys
from time import sleep
from typing import Any, Callable, TypedDict, cast

from firebase_admin.exceptions import FirebaseError
from google.api_core.exceptions import GoogleAPICallError, RetryError
from google.cloud import firestore

# ── project root on sys.path ────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.coercion import coerce_optional_str  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.firestore_constants import (  # noqa: E402
    AI_CREDITS_CURRENT_DOCUMENT_ID,
    AI_CREDITS_SUBCOLLECTION,
    AI_CREDIT_TRANSACTIONS_SUBCOLLECTION,
    BILLING_DOCUMENT_ID,
    BILLING_SUBCOLLECTION,
    MEALS_SUBCOLLECTION,
    USERS_COLLECTION,
)
from app.db.firebase import get_firestore, init_firebase  # noqa: E402
from app.services.meal_service import normalize_meal_document_payload  # noqa: E402

LEGACY_AI_CREDITS_COLLECTION = "ai_credits"
LEGACY_AI_CREDIT_TRANSACTIONS_COLLECTION = "ai_credit_transactions"
DOCUMENT_ID_FIELD = "__name__"
SCHEMA_VERSION = 2
DEFAULT_STATE_FILE = Path(".migration_state/firestore_v2_backfill_state.json")
LEGACY_MEAL_FIELDS_TO_DELETE = (
    "id",
    "mealId",
    "cloudId",
    "userUid",
    "timestamp",
    "imageId",
    "photoUrl",
    "photoLocalPath",
    "syncState",
)
RETRYABLE_EXCEPTIONS = (FirebaseError, GoogleAPICallError, RetryError)
PHASE_AI_CREDITS = "aiCreditsSnapshot"
PHASE_AI_CREDIT_TRANSACTIONS = "aiCreditTransactions"
PHASE_MEALS = "meals"
PHASE_DONE = "done"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("migrate_firestore_v2_backfill")


class MigrationState(TypedDict):
    phase: str
    aiCreditsCursor: str | None
    aiCreditTransactionsCursor: str | None
    mealsUserCursor: str | None
    mealsActiveUserId: str | None
    mealsMealCursor: str | None


@dataclass
class PhaseReport:
    scanned: int = 0
    migrated: int = 0
    skipped: int = 0
    manualIntervention: int = 0
    legacyDeleted: int = 0


@dataclass
class ManualInterventionItem:
    phase: str
    documentPath: str
    reason: str


@dataclass
class MigrationReport:
    startedAt: str
    dryRun: bool
    deleteLegacy: bool
    batchSize: int
    writeBatchSize: int
    maxRetries: int
    stateFile: str
    phases: dict[str, PhaseReport] = field(
        default_factory=lambda: {
            PHASE_AI_CREDITS: PhaseReport(),
            PHASE_AI_CREDIT_TRANSACTIONS: PhaseReport(),
            PHASE_MEALS: PhaseReport(),
        }
    )
    manualInterventions: list[ManualInterventionItem] = field(default_factory=list)
    endedAt: str | None = None

    def add_manual(
        self,
        *,
        phase: str,
        document_path: str,
        reason: str,
    ) -> None:
        self.phases[phase].manualIntervention += 1
        if len(self.manualInterventions) >= 100:
            return
        self.manualInterventions.append(
            ManualInterventionItem(
                phase=phase,
                documentPath=document_path,
                reason=reason,
            )
        )

    def finish(self) -> None:
        self.endedAt = _utc_iso()

    def to_dict(self) -> dict[str, Any]:
        totals = PhaseReport()
        for phase_report in self.phases.values():
            totals.scanned += phase_report.scanned
            totals.migrated += phase_report.migrated
            totals.skipped += phase_report.skipped
            totals.manualIntervention += phase_report.manualIntervention
            totals.legacyDeleted += phase_report.legacyDeleted
        return {
            "startedAt": self.startedAt,
            "endedAt": self.endedAt,
            "dryRun": self.dryRun,
            "deleteLegacy": self.deleteLegacy,
            "batchSize": self.batchSize,
            "writeBatchSize": self.writeBatchSize,
            "maxRetries": self.maxRetries,
            "stateFile": self.stateFile,
            "totals": asdict(totals),
            "phases": {name: asdict(report) for name, report in self.phases.items()},
            "manualInterventions": [asdict(item) for item in self.manualInterventions],
        }


class FirestoreBatchWriter:
    def __init__(
        self,
        *,
        client: firestore.Client,
        dry_run: bool,
        write_batch_size: int,
        max_retries: int,
    ) -> None:
        self._client = client
        self._dry_run = dry_run
        self._write_batch_size = max(1, write_batch_size)
        self._max_retries = max(1, max_retries)
        self._batch = self._client.batch()
        self._operations = 0

    def set(self, reference: Any, payload: dict[str, Any], *, merge: bool) -> None:
        if self._dry_run:
            return
        self._batch.set(reference, payload, merge=merge)
        self._operations += 1
        if self._operations >= self._write_batch_size:
            self.commit()

    def delete(self, reference: Any) -> None:
        if self._dry_run:
            return
        self._batch.delete(reference)
        self._operations += 1
        if self._operations >= self._write_batch_size:
            self.commit()

    def commit(self) -> None:
        if self._dry_run or self._operations <= 0:
            return

        batch = self._batch
        ops_count = self._operations
        _run_with_retry(
            lambda: batch.commit(),
            description=f"commit Firestore write batch ({ops_count} ops)",
            max_retries=self._max_retries,
        )
        self._batch = self._client.batch()
        self._operations = 0


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_iso() -> str:
    return _utc_now().isoformat().replace("+00:00", "Z")


def default_state() -> MigrationState:
    return {
        "phase": PHASE_AI_CREDITS,
        "aiCreditsCursor": None,
        "aiCreditTransactionsCursor": None,
        "mealsUserCursor": None,
        "mealsActiveUserId": None,
        "mealsMealCursor": None,
    }


def load_state(*, state_file: Path, reset: bool) -> MigrationState:
    if reset or not state_file.exists():
        return default_state()
    payload = json.loads(state_file.read_text(encoding="utf-8"))
    state = default_state()
    for key in state.keys():
        if key in payload:
            state[key] = payload[key]
    return state


def save_state(*, state_file: Path, state: MigrationState) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _run_with_retry(
    operation: Callable[[], Any],
    *,
    description: str,
    max_retries: int,
) -> Any:
    capped_retries = max(1, max_retries)
    for attempt in range(1, capped_retries + 1):
        try:
            return operation()
        except RETRYABLE_EXCEPTIONS:
            if attempt >= capped_retries:
                logger.exception("Operation failed after retries: %s", description)
                raise
            delay_seconds = min(0.5 * (2 ** (attempt - 1)), 8.0)
            logger.warning(
                "Retryable Firestore error (%s), retrying in %.2fs [attempt=%d/%d]",
                description,
                delay_seconds,
                attempt,
                capped_retries,
            )
            sleep(delay_seconds)
            continue


def _billing_root_ref(client: firestore.Client, user_id: str) -> firestore.DocumentReference:
    return (
        client.collection(USERS_COLLECTION)
        .document(user_id)
        .collection(BILLING_SUBCOLLECTION)
        .document(BILLING_DOCUMENT_ID)
    )


def _billing_ai_credits_current_ref(
    client: firestore.Client,
    user_id: str,
) -> firestore.DocumentReference:
    return (
        _billing_root_ref(client, user_id)
        .collection(AI_CREDITS_SUBCOLLECTION)
        .document(AI_CREDITS_CURRENT_DOCUMENT_ID)
    )


def _billing_ai_credit_transactions_ref(
    client: firestore.Client,
    user_id: str,
) -> firestore.CollectionReference:
    return _billing_root_ref(client, user_id).collection(AI_CREDIT_TRANSACTIONS_SUBCOLLECTION)


def _list_documents_page(
    collection_ref: firestore.CollectionReference,
    *,
    start_after_id: str | None,
    limit: int,
    max_retries: int,
    description: str,
) -> list[firestore.DocumentSnapshot]:
    query = collection_ref.order_by(
        DOCUMENT_ID_FIELD,
        direction=firestore.Query.ASCENDING,
    ).limit(limit)
    if start_after_id:
        query = query.start_after([start_after_id])
    return cast(
        list[firestore.DocumentSnapshot],
        _run_with_retry(
            lambda: list(query.stream()),
            description=description,
            max_retries=max_retries,
        ),
    )


def _billing_root_metadata(*, migrated_at_iso: str) -> dict[str, Any]:
    return {
        "namespace": "ai_billing",
        "schemaVersion": SCHEMA_VERSION,
        "migratedAt": migrated_at_iso,
        "updatedAt": _utc_now(),
    }


def normalize_legacy_credit_snapshot_payload(
    payload: dict[str, Any],
    *,
    migrated_at_iso: str,
) -> dict[str, Any]:
    normalized = dict(payload)
    normalized.pop("userId", None)
    normalized["schemaVersion"] = SCHEMA_VERSION
    normalized["migratedAt"] = migrated_at_iso
    return normalized


def normalize_legacy_credit_transaction_payload(
    payload: dict[str, Any],
    *,
    migrated_at_iso: str,
) -> dict[str, Any]:
    normalized = dict(payload)
    normalized.pop("userId", None)
    normalized["schemaVersion"] = SCHEMA_VERSION
    normalized["migratedAt"] = migrated_at_iso
    return normalized


def is_credit_snapshot_migrated(payload: dict[str, Any]) -> bool:
    return payload.get("schemaVersion") == SCHEMA_VERSION and "userId" not in payload


def is_credit_transaction_migrated(payload: dict[str, Any]) -> bool:
    return payload.get("schemaVersion") == SCHEMA_VERSION and "userId" not in payload


def _derive_fallback_day_key(payload: dict[str, Any]) -> str | None:
    direct_day_key = coerce_optional_str(payload.get("dayKey"))
    if direct_day_key:
        return direct_day_key
    for field_name in ("loggedAt", "timestamp"):
        value = coerce_optional_str(payload.get(field_name))
        if value and len(value) >= 10:
            return value[:10]
    return None


def _derive_fallback_updated_at(payload: dict[str, Any], *, now_iso: str) -> str:
    for field_name in ("updatedAt", "loggedAt", "timestamp", "createdAt"):
        value = coerce_optional_str(payload.get(field_name))
        if value:
            return value
    return now_iso


def normalize_legacy_meal_payload(
    *,
    user_id: str,
    meal_doc_id: str,
    payload: dict[str, Any],
    migrated_at_iso: str,
) -> dict[str, Any]:
    payload_with_doc_id = dict(payload)
    payload_with_doc_id["id"] = meal_doc_id
    now_iso = _utc_iso()
    _, canonical_document = normalize_meal_document_payload(
        user_id,
        payload_with_doc_id,
        fallback_cloud_id=meal_doc_id,
        fallback_updated_at=_derive_fallback_updated_at(payload_with_doc_id, now_iso=now_iso),
        fallback_day_key=_derive_fallback_day_key(payload_with_doc_id),
    )
    canonical_document["schemaVersion"] = SCHEMA_VERSION
    canonical_document["migratedAt"] = migrated_at_iso
    return canonical_document


def build_meal_migration_update_payload(
    canonical_document: dict[str, Any],
) -> dict[str, Any]:
    payload = dict(canonical_document)
    for legacy_key in LEGACY_MEAL_FIELDS_TO_DELETE:
        payload[legacy_key] = firestore.DELETE_FIELD
    return payload


def is_meal_already_migrated(payload: dict[str, Any]) -> bool:
    if payload.get("schemaVersion") != SCHEMA_VERSION:
        return False
    if not coerce_optional_str(payload.get("loggedAt")):
        return False
    if not coerce_optional_str(payload.get("createdAt")):
        return False
    if not coerce_optional_str(payload.get("updatedAt")):
        return False
    for legacy_key in LEGACY_MEAL_FIELDS_TO_DELETE:
        if legacy_key in payload:
            return False
    return True


def _migrate_ai_credit_snapshots(
    *,
    client: firestore.Client,
    writer: FirestoreBatchWriter,
    state: MigrationState,
    state_file: Path,
    report: MigrationReport,
    batch_size: int,
    max_retries: int,
    dry_run: bool,
    delete_legacy: bool,
    migrated_at_iso: str,
) -> None:
    phase = PHASE_AI_CREDITS
    cursor = coerce_optional_str(state.get("aiCreditsCursor"))
    collection_ref = client.collection(LEGACY_AI_CREDITS_COLLECTION)

    while True:
        snapshots = _list_documents_page(
            collection_ref,
            start_after_id=cursor,
            limit=batch_size,
            max_retries=max_retries,
            description=f"read {LEGACY_AI_CREDITS_COLLECTION} page",
        )
        if not snapshots:
            break

        for snapshot in snapshots:
            report.phases[phase].scanned += 1
            user_id = snapshot.id
            legacy_payload = dict(snapshot.to_dict() or {})
            target_ref = _billing_ai_credits_current_ref(client, user_id)
            target_snapshot = cast(
                firestore.DocumentSnapshot,
                _run_with_retry(
                    lambda: target_ref.get(),
                    description=f"read target credits doc for uid={user_id}",
                    max_retries=max_retries,
                ),
            )
            if target_snapshot.exists and is_credit_snapshot_migrated(dict(target_snapshot.to_dict() or {})):
                report.phases[phase].skipped += 1
                if delete_legacy:
                    writer.delete(snapshot.reference)
                    report.phases[phase].legacyDeleted += 1
                continue

            normalized_payload = normalize_legacy_credit_snapshot_payload(
                legacy_payload,
                migrated_at_iso=migrated_at_iso,
            )
            report.phases[phase].migrated += 1
            if dry_run:
                continue

            writer.set(
                _billing_root_ref(client, user_id),
                _billing_root_metadata(migrated_at_iso=migrated_at_iso),
                merge=True,
            )
            writer.set(target_ref, normalized_payload, merge=True)
            if delete_legacy:
                writer.delete(snapshot.reference)
                report.phases[phase].legacyDeleted += 1

        cursor = snapshots[-1].id
        state["aiCreditsCursor"] = cursor
        save_state(state_file=state_file, state=state)
        writer.commit()

    state["phase"] = PHASE_AI_CREDIT_TRANSACTIONS
    save_state(state_file=state_file, state=state)


def _migrate_ai_credit_transactions(
    *,
    client: firestore.Client,
    writer: FirestoreBatchWriter,
    state: MigrationState,
    state_file: Path,
    report: MigrationReport,
    batch_size: int,
    max_retries: int,
    dry_run: bool,
    delete_legacy: bool,
    migrated_at_iso: str,
) -> None:
    phase = PHASE_AI_CREDIT_TRANSACTIONS
    cursor = coerce_optional_str(state.get("aiCreditTransactionsCursor"))
    collection_ref = client.collection(LEGACY_AI_CREDIT_TRANSACTIONS_COLLECTION)

    while True:
        snapshots = _list_documents_page(
            collection_ref,
            start_after_id=cursor,
            limit=batch_size,
            max_retries=max_retries,
            description=f"read {LEGACY_AI_CREDIT_TRANSACTIONS_COLLECTION} page",
        )
        if not snapshots:
            break

        for snapshot in snapshots:
            report.phases[phase].scanned += 1
            tx_id = snapshot.id
            legacy_payload = dict(snapshot.to_dict() or {})
            user_id = coerce_optional_str(legacy_payload.get("userId"))
            if not user_id:
                report.add_manual(
                    phase=phase,
                    document_path=snapshot.reference.path,
                    reason="Missing userId in legacy transaction payload.",
                )
                continue

            tx_ref = _billing_ai_credit_transactions_ref(client, user_id).document(tx_id)
            target_snapshot = cast(
                firestore.DocumentSnapshot,
                _run_with_retry(
                    lambda: tx_ref.get(),
                    description=f"read target tx doc for uid={user_id} tx={tx_id}",
                    max_retries=max_retries,
                ),
            )
            if target_snapshot.exists and is_credit_transaction_migrated(dict(target_snapshot.to_dict() or {})):
                report.phases[phase].skipped += 1
                if delete_legacy:
                    writer.delete(snapshot.reference)
                    report.phases[phase].legacyDeleted += 1
                continue

            normalized_payload = normalize_legacy_credit_transaction_payload(
                legacy_payload,
                migrated_at_iso=migrated_at_iso,
            )
            report.phases[phase].migrated += 1
            if dry_run:
                continue

            writer.set(
                _billing_root_ref(client, user_id),
                _billing_root_metadata(migrated_at_iso=migrated_at_iso),
                merge=True,
            )
            writer.set(tx_ref, normalized_payload, merge=True)
            if delete_legacy:
                writer.delete(snapshot.reference)
                report.phases[phase].legacyDeleted += 1

        cursor = snapshots[-1].id
        state["aiCreditTransactionsCursor"] = cursor
        save_state(state_file=state_file, state=state)
        writer.commit()

    state["phase"] = PHASE_MEALS
    save_state(state_file=state_file, state=state)


def _migrate_meals_for_single_user(
    *,
    client: firestore.Client,
    user_id: str,
    start_after_meal_id: str | None,
    writer: FirestoreBatchWriter,
    state: MigrationState,
    state_file: Path,
    report: MigrationReport,
    batch_size: int,
    max_retries: int,
    dry_run: bool,
    migrated_at_iso: str,
) -> None:
    phase = PHASE_MEALS
    state["mealsActiveUserId"] = user_id
    state["mealsMealCursor"] = start_after_meal_id
    save_state(state_file=state_file, state=state)

    meals_collection_ref = (
        client.collection(USERS_COLLECTION).document(user_id).collection(MEALS_SUBCOLLECTION)
    )
    meal_cursor = start_after_meal_id

    while True:
        meal_snapshots = _list_documents_page(
            meals_collection_ref,
            start_after_id=meal_cursor,
            limit=batch_size,
            max_retries=max_retries,
            description=f"read meals page for uid={user_id}",
        )
        if not meal_snapshots:
            break

        for meal_snapshot in meal_snapshots:
            report.phases[phase].scanned += 1
            meal_payload = dict(meal_snapshot.to_dict() or {})
            if is_meal_already_migrated(meal_payload):
                report.phases[phase].skipped += 1
                continue

            try:
                canonical_document = normalize_legacy_meal_payload(
                    user_id=user_id,
                    meal_doc_id=meal_snapshot.id,
                    payload=meal_payload,
                    migrated_at_iso=migrated_at_iso,
                )
                update_payload = build_meal_migration_update_payload(canonical_document)
            except Exception as exc:  # noqa: BLE001
                report.add_manual(
                    phase=phase,
                    document_path=meal_snapshot.reference.path,
                    reason=f"Meal normalization failed: {exc}",
                )
                continue

            report.phases[phase].migrated += 1
            if dry_run:
                continue
            writer.set(meal_snapshot.reference, update_payload, merge=True)

        meal_cursor = meal_snapshots[-1].id
        state["mealsMealCursor"] = meal_cursor
        save_state(state_file=state_file, state=state)
        writer.commit()

    state["mealsActiveUserId"] = None
    state["mealsMealCursor"] = None
    state["mealsUserCursor"] = user_id
    save_state(state_file=state_file, state=state)


def _migrate_meals(
    *,
    client: firestore.Client,
    writer: FirestoreBatchWriter,
    state: MigrationState,
    state_file: Path,
    report: MigrationReport,
    batch_size: int,
    max_retries: int,
    dry_run: bool,
    migrated_at_iso: str,
) -> None:
    active_user_id = coerce_optional_str(state.get("mealsActiveUserId"))
    active_meal_cursor = coerce_optional_str(state.get("mealsMealCursor"))
    if active_user_id:
        _migrate_meals_for_single_user(
            client=client,
            user_id=active_user_id,
            start_after_meal_id=active_meal_cursor,
            writer=writer,
            state=state,
            state_file=state_file,
            report=report,
            batch_size=batch_size,
            max_retries=max_retries,
            dry_run=dry_run,
            migrated_at_iso=migrated_at_iso,
        )

    user_cursor = coerce_optional_str(state.get("mealsUserCursor"))
    users_collection_ref = client.collection(USERS_COLLECTION)

    while True:
        user_snapshots = _list_documents_page(
            users_collection_ref,
            start_after_id=user_cursor,
            limit=batch_size,
            max_retries=max_retries,
            description="read users page for meals migration",
        )
        if not user_snapshots:
            break

        for user_snapshot in user_snapshots:
            user_id = user_snapshot.id
            _migrate_meals_for_single_user(
                client=client,
                user_id=user_id,
                start_after_meal_id=None,
                writer=writer,
                state=state,
                state_file=state_file,
                report=report,
                batch_size=batch_size,
                max_retries=max_retries,
                dry_run=dry_run,
                migrated_at_iso=migrated_at_iso,
            )
            user_cursor = user_id

    state["phase"] = PHASE_DONE
    save_state(state_file=state_file, state=state)


def run_migration(
    *,
    client: firestore.Client,
    dry_run: bool,
    delete_legacy: bool,
    batch_size: int,
    write_batch_size: int,
    max_retries: int,
    state_file: Path,
    reset_state: bool,
) -> MigrationReport:
    state = load_state(state_file=state_file, reset=reset_state)
    save_state(state_file=state_file, state=state)

    report = MigrationReport(
        startedAt=_utc_iso(),
        dryRun=dry_run,
        deleteLegacy=delete_legacy,
        batchSize=batch_size,
        writeBatchSize=write_batch_size,
        maxRetries=max_retries,
        stateFile=str(state_file),
    )
    writer = FirestoreBatchWriter(
        client=client,
        dry_run=dry_run,
        write_batch_size=write_batch_size,
        max_retries=max_retries,
    )
    migrated_at_iso = _utc_iso()

    logger.info(
        "Starting Firestore v2 backfill: dry_run=%s delete_legacy=%s phase=%s project=%s env=%s",
        dry_run,
        delete_legacy,
        state["phase"],
        settings.FIREBASE_PROJECT_ID or "(unset)",
        settings.ENVIRONMENT,
    )

    if state["phase"] == PHASE_AI_CREDITS:
        _migrate_ai_credit_snapshots(
            client=client,
            writer=writer,
            state=state,
            state_file=state_file,
            report=report,
            batch_size=batch_size,
            max_retries=max_retries,
            dry_run=dry_run,
            delete_legacy=delete_legacy,
            migrated_at_iso=migrated_at_iso,
        )

    if state["phase"] == PHASE_AI_CREDIT_TRANSACTIONS:
        _migrate_ai_credit_transactions(
            client=client,
            writer=writer,
            state=state,
            state_file=state_file,
            report=report,
            batch_size=batch_size,
            max_retries=max_retries,
            dry_run=dry_run,
            delete_legacy=delete_legacy,
            migrated_at_iso=migrated_at_iso,
        )

    if state["phase"] == PHASE_MEALS:
        _migrate_meals(
            client=client,
            writer=writer,
            state=state,
            state_file=state_file,
            report=report,
            batch_size=batch_size,
            max_retries=max_retries,
            dry_run=dry_run,
            migrated_at_iso=migrated_at_iso,
        )

    writer.commit()
    report.finish()
    logger.info("Finished Firestore v2 backfill phase=%s", state["phase"])
    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Do not write/delete data.")
    parser.add_argument(
        "--delete-legacy",
        action="store_true",
        help="Delete legacy ai_credits and ai_credit_transactions after migration.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=200,
        help="Read page size for each collection scan.",
    )
    parser.add_argument(
        "--write-batch-size",
        type=int,
        default=200,
        help="Maximum number of write operations per Firestore batch commit.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=5,
        help="Max retries for retryable Firestore calls.",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=DEFAULT_STATE_FILE,
        help=f"Resume state file path (default: {DEFAULT_STATE_FILE}).",
    )
    parser.add_argument(
        "--reset-state",
        action="store_true",
        help="Ignore previous state file and start from phase 1.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Confirm non-dry-run execution.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if not args.dry_run and not args.yes:
        raise SystemExit("Refusing to run write migration without --yes. Use --dry-run first.")
    if args.batch_size <= 0:
        raise SystemExit("--batch-size must be > 0")
    if args.write_batch_size <= 0:
        raise SystemExit("--write-batch-size must be > 0")
    if args.max_retries <= 0:
        raise SystemExit("--max-retries must be > 0")

    init_firebase()
    client = get_firestore()
    report = run_migration(
        client=client,
        dry_run=args.dry_run,
        delete_legacy=args.delete_legacy,
        batch_size=args.batch_size,
        write_batch_size=args.write_batch_size,
        max_retries=args.max_retries,
        state_file=args.state_file,
        reset_state=args.reset_state,
    )
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
