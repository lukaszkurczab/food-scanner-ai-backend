"""Unit tests for scripts/migrate_firestore_v2_backfill.py helpers."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

from google.cloud import firestore

# Make scripts importable when tests run from repository root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import scripts.migrate_firestore_v2_backfill as migration  # noqa: E402


def test_normalize_legacy_credit_snapshot_payload_strips_user_id_and_sets_metadata() -> None:
    payload: dict[str, Any] = {
        "userId": "user-1",
        "tier": "free",
        "balance": 42,
    }
    normalized = migration.normalize_legacy_credit_snapshot_payload(
        payload,
        migrated_at_iso="2026-04-23T10:00:00Z",
    )
    assert "userId" not in normalized
    assert normalized["tier"] == "free"
    assert normalized["balance"] == 42
    assert normalized["schemaVersion"] == migration.SCHEMA_VERSION
    assert normalized["migratedAt"] == "2026-04-23T10:00:00Z"


def test_normalize_legacy_credit_transaction_payload_strips_user_id_and_sets_metadata() -> None:
    payload: dict[str, Any] = {
        "userId": "user-1",
        "type": "deduct",
        "cost": 1,
    }
    normalized = migration.normalize_legacy_credit_transaction_payload(
        payload,
        migrated_at_iso="2026-04-23T10:00:00Z",
    )
    assert "userId" not in normalized
    assert normalized["type"] == "deduct"
    assert normalized["cost"] == 1
    assert normalized["schemaVersion"] == migration.SCHEMA_VERSION
    assert normalized["migratedAt"] == "2026-04-23T10:00:00Z"


def test_normalize_legacy_meal_payload_maps_timestamp_and_builds_image_ref() -> None:
    payload: dict[str, Any] = {
        "id": "legacy-id",
        "mealId": "legacy-id",
        "cloudId": "cloud-id",
        "userUid": "user-1",
        "photoLocalPath": "file:///var/mobile/photo.jpg",
        "timestamp": "2026-04-20T08:45:00Z",
        "updatedAt": "2026-04-20T09:00:00Z",
        "createdAt": "2026-04-20T08:40:00Z",
        "type": "breakfast",
        "name": "Owsianka",
        "imageId": "img-123",
        "photoUrl": "https://cdn.example.com/img-123.jpg",
        "ingredients": [
            {
                "id": "ing-1",
                "name": "Platki",
                "amount": 80,
                "unit": "g",
                "kcal": 280,
                "protein": 9,
                "fat": 4,
                "carbs": 52,
            }
        ],
    }

    normalized = migration.normalize_legacy_meal_payload(
        user_id="user-1",
        meal_doc_id="meal-doc-1",
        payload=payload,
        migrated_at_iso="2026-04-23T10:00:00Z",
    )

    assert normalized["loggedAt"] == "2026-04-20T08:45:00Z"
    assert normalized["createdAt"] == "2026-04-20T08:40:00Z"
    assert normalized["updatedAt"] == "2026-04-20T09:00:00Z"
    assert normalized["type"] == "breakfast"
    assert normalized["imageRef"] == {
        "imageId": "img-123",
        "storagePath": "meals/user-1/img-123.jpg",
        "downloadUrl": "https://cdn.example.com/img-123.jpg",
    }
    assert "mealId" not in normalized
    assert "cloudId" not in normalized
    assert "timestamp" not in normalized
    assert "photoLocalPath" not in normalized
    assert normalized["schemaVersion"] == migration.SCHEMA_VERSION
    assert normalized["migratedAt"] == "2026-04-23T10:00:00Z"


def test_build_meal_migration_update_payload_marks_legacy_fields_for_deletion() -> None:
    canonical_document: dict[str, Any] = {
        "loggedAt": "2026-04-20T08:45:00Z",
        "createdAt": "2026-04-20T08:40:00Z",
        "updatedAt": "2026-04-20T09:00:00Z",
        "type": "other",
        "schemaVersion": migration.SCHEMA_VERSION,
        "migratedAt": "2026-04-23T10:00:00Z",
    }
    update_payload = migration.build_meal_migration_update_payload(canonical_document)

    for legacy_key in migration.LEGACY_MEAL_FIELDS_TO_DELETE:
        assert update_payload[legacy_key] is firestore.DELETE_FIELD
    assert update_payload["loggedAt"] == "2026-04-20T08:45:00Z"
    assert update_payload["schemaVersion"] == migration.SCHEMA_VERSION


def test_is_meal_already_migrated_requires_schema_and_no_legacy_fields() -> None:
    canonical: dict[str, Any] = {
        "loggedAt": "2026-04-20T08:45:00Z",
        "createdAt": "2026-04-20T08:40:00Z",
        "updatedAt": "2026-04-20T09:00:00Z",
        "type": "other",
        "schemaVersion": migration.SCHEMA_VERSION,
    }
    assert migration.is_meal_already_migrated(canonical) is True

    with_legacy_key: dict[str, Any] = dict(canonical)
    with_legacy_key["timestamp"] = "2026-04-20T08:45:00Z"
    assert migration.is_meal_already_migrated(with_legacy_key) is False

    without_schema: dict[str, Any] = dict(canonical)
    without_schema.pop("schemaVersion")
    assert migration.is_meal_already_migrated(without_schema) is False


def test_state_roundtrip_load_and_save(tmp_path: Path) -> None:
    state_file = tmp_path / "resume-state.json"
    original = migration.default_state()
    original["phase"] = migration.PHASE_MEALS
    original["mealsActiveUserId"] = "user-123"
    original["mealsMealCursor"] = "meal-456"
    migration.save_state(state_file=state_file, state=original)

    loaded = migration.load_state(state_file=state_file, reset=False)
    assert loaded == original

    reset_loaded = migration.load_state(state_file=state_file, reset=True)
    assert reset_loaded == migration.default_state()


def test_report_to_dict_includes_totals_and_manual_items() -> None:
    report = migration.MigrationReport(
        startedAt="2026-04-23T10:00:00Z",
        dryRun=True,
        deleteLegacy=False,
        batchSize=200,
        writeBatchSize=200,
        maxRetries=5,
        stateFile=".migration_state/firestore_v2_backfill_state.json",
    )
    report.phases[migration.PHASE_AI_CREDITS].scanned = 10
    report.phases[migration.PHASE_AI_CREDITS].migrated = 4
    report.phases[migration.PHASE_AI_CREDITS].skipped = 6
    report.add_manual(
        phase=migration.PHASE_MEALS,
        document_path="users/u1/meals/m1",
        reason="invalid timestamp",
    )
    report.finish()
    payload = report.to_dict()

    assert payload["totals"]["scanned"] == 10
    assert payload["totals"]["migrated"] == 4
    assert payload["totals"]["skipped"] == 6
    assert payload["totals"]["manualIntervention"] == 1
    assert payload["manualInterventions"][0]["documentPath"] == "users/u1/meals/m1"
    assert payload["manualInterventions"][0]["reason"] == "invalid timestamp"
    # Ensure payload is JSON-serializable for CLI output.
    json.dumps(payload)
