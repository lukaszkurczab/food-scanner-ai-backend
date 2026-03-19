import pytest
from pydantic import ValidationError

from app.schemas.meal import MealAiMeta, MealItem, MealTotals, MealUpsertRequest


# ---------------------------------------------------------------------------
# inputMethod & aiMeta (existing)
# ---------------------------------------------------------------------------


def test_meal_upsert_request_accepts_input_method_and_ai_meta() -> None:
    payload = MealUpsertRequest.model_validate(
        {
            "mealId": "meal-1",
            "timestamp": "2026-03-18T12:00:00.000Z",
            "type": "lunch",
            "ingredients": [],
            "inputMethod": "photo",
            "aiMeta": {
                "model": "gpt-4o-mini",
                "runId": "run-1",
                "confidence": 0.92,
            },
        }
    )

    assert payload.inputMethod == "photo"
    assert payload.aiMeta is not None
    assert payload.aiMeta.model == "gpt-4o-mini"
    assert payload.aiMeta.runId == "run-1"
    assert payload.aiMeta.confidence == 0.92
    assert payload.aiMeta.warnings == []


def test_meal_upsert_request_is_backward_compatible_without_new_fields() -> None:
    payload = MealUpsertRequest.model_validate(
        {
            "mealId": "meal-1",
            "timestamp": "2026-03-18T12:00:00.000Z",
            "type": "lunch",
            "ingredients": [],
        }
    )

    assert payload.inputMethod is None
    assert payload.aiMeta is None


def test_meal_item_serializes_input_method_and_ai_meta() -> None:
    item = MealItem.model_validate(
        {
            "userUid": "user-1",
            "mealId": "meal-1",
            "timestamp": "2026-03-18T12:00:00.000Z",
            "type": "lunch",
            "ingredients": [],
            "createdAt": "2026-03-18T12:00:00.000Z",
            "updatedAt": "2026-03-18T12:05:00.000Z",
            "cloudId": "meal-1",
            "inputMethod": "text",
            "aiMeta": {
                "model": "gpt-4o-mini",
                "runId": None,
                "confidence": 0.71,
                "warnings": ["partial_totals"],
            },
        }
    )

    assert item.model_dump()["inputMethod"] == "text"
    assert item.model_dump()["aiMeta"] == {
        "model": "gpt-4o-mini",
        "runId": None,
        "confidence": 0.71,
        "warnings": ["partial_totals"],
    }


def test_meal_upsert_request_rejects_invalid_input_method() -> None:
    with pytest.raises(ValidationError):
        MealUpsertRequest.model_validate(
            {
                "mealId": "meal-1",
                "timestamp": "2026-03-18T12:00:00.000Z",
                "type": "lunch",
                "ingredients": [],
                "inputMethod": "voice",
            }
        )


# ---------------------------------------------------------------------------
# syncState parity — mobile defines "synced" | "pending" | "conflict" | "failed"
# ---------------------------------------------------------------------------


def test_meal_item_accepts_all_sync_states() -> None:
    """Backend schema must accept every syncState that mobile can produce."""
    base = {
        "userUid": "user-1",
        "mealId": "meal-1",
        "timestamp": "2026-03-18T12:00:00.000Z",
        "type": "lunch",
        "ingredients": [],
        "createdAt": "2026-03-18T12:00:00.000Z",
        "updatedAt": "2026-03-18T12:00:00.000Z",
        "cloudId": "meal-1",
    }
    for state in ("synced", "pending", "conflict", "failed"):
        item = MealItem.model_validate({**base, "syncState": state})
        assert item.syncState == state


def test_meal_upsert_request_accepts_all_sync_states() -> None:
    """Request model must accept every syncState that mobile can send."""
    base = {
        "mealId": "meal-1",
        "timestamp": "2026-03-18T12:00:00.000Z",
        "type": "lunch",
        "ingredients": [],
    }
    for state in ("synced", "pending", "conflict", "failed"):
        req = MealUpsertRequest.model_validate({**base, "syncState": state})
        assert req.syncState == state


def test_meal_item_rejects_unknown_sync_state() -> None:
    with pytest.raises(ValidationError):
        MealItem.model_validate(
            {
                "userUid": "user-1",
                "mealId": "meal-1",
                "timestamp": "2026-03-18T12:00:00.000Z",
                "type": "lunch",
                "ingredients": [],
                "createdAt": "2026-03-18T12:00:00.000Z",
                "updatedAt": "2026-03-18T12:00:00.000Z",
                "cloudId": "meal-1",
                "syncState": "broken",
            }
        )


def test_meal_upsert_request_rejects_unknown_sync_state() -> None:
    with pytest.raises(ValidationError):
        MealUpsertRequest.model_validate(
            {
                "mealId": "meal-1",
                "timestamp": "2026-03-18T12:00:00.000Z",
                "type": "lunch",
                "ingredients": [],
                "syncState": "broken",
            }
        )


# ---------------------------------------------------------------------------
# Full boundary contract — request parse with complete payload
# ---------------------------------------------------------------------------

_FULL_MEAL_PAYLOAD = {
    "mealId": "meal-full-1",
    "timestamp": "2026-03-18T12:00:00.000Z",
    "dayKey": "2026-03-18",
    "type": "lunch",
    "name": "Grilled chicken salad",
    "ingredients": [
        {
            "id": "ing-1",
            "name": "Chicken breast",
            "amount": 200.0,
            "unit": "g",
            "kcal": 330.0,
            "protein": 62.0,
            "fat": 7.2,
            "carbs": 0.0,
        }
    ],
    "createdAt": "2026-03-18T12:00:00.000Z",
    "updatedAt": "2026-03-18T12:05:00.000Z",
    "syncState": "synced",
    "source": "ai",
    "inputMethod": "photo",
    "aiMeta": {
        "model": "gpt-4o",
        "runId": "run-abc",
        "confidence": 0.88,
        "warnings": ["partial_totals"],
    },
    "imageId": "img-001",
    "photoUrl": "https://storage.example.com/photo.jpg",
    "notes": "Post-workout meal",
    "tags": ["high-protein", "lunch"],
    "deleted": False,
    "cloudId": "meal-full-1",
    "totals": {"kcal": 330.0, "protein": 62.0, "fat": 7.2, "carbs": 0.0},
}


def test_full_meal_request_parses_all_boundary_fields() -> None:
    """Complete meal payload with all fields round-trips through request model."""
    req = MealUpsertRequest.model_validate(_FULL_MEAL_PAYLOAD)

    assert req.mealId == "meal-full-1"
    assert req.dayKey == "2026-03-18"
    assert req.type == "lunch"
    assert req.name == "Grilled chicken salad"
    assert len(req.ingredients) == 1
    assert req.ingredients[0].id == "ing-1"
    assert req.ingredients[0].protein == 62.0
    assert req.syncState == "synced"
    assert req.source == "ai"
    assert req.inputMethod == "photo"
    assert req.aiMeta is not None
    assert req.aiMeta.model == "gpt-4o"
    assert req.aiMeta.confidence == 0.88
    assert req.aiMeta.warnings == ["partial_totals"]
    assert req.imageId == "img-001"
    assert req.notes == "Post-workout meal"
    assert req.tags == ["high-protein", "lunch"]
    assert req.deleted is False
    assert req.totals is not None
    assert req.totals.kcal == 330.0


def test_full_meal_response_serializes_all_boundary_fields() -> None:
    """Complete MealItem round-trips through response model and serialization."""
    item = MealItem.model_validate(
        {**_FULL_MEAL_PAYLOAD, "userUid": "user-1"}
    )
    data = item.model_dump()

    assert data["userUid"] == "user-1"
    assert data["mealId"] == "meal-full-1"
    assert data["dayKey"] == "2026-03-18"
    assert data["type"] == "lunch"
    assert data["source"] == "ai"
    assert data["inputMethod"] == "photo"
    assert data["aiMeta"]["model"] == "gpt-4o"
    assert data["aiMeta"]["confidence"] == 0.88
    assert data["syncState"] == "synced"
    assert data["totals"]["kcal"] == 330.0
    assert data["totals"]["protein"] == 62.0
    assert data["notes"] == "Post-workout meal"
    assert data["tags"] == ["high-protein", "lunch"]
    assert data["deleted"] is False


# ---------------------------------------------------------------------------
# Backward compatibility — old payload without Foundation Sprint fields
# ---------------------------------------------------------------------------


def test_legacy_payload_without_foundation_fields_still_works() -> None:
    """Pre-Foundation-Sprint payload (no inputMethod, aiMeta, dayKey) must parse."""
    legacy = MealUpsertRequest.model_validate(
        {
            "mealId": "legacy-1",
            "timestamp": "2025-12-01T08:00:00.000Z",
            "type": "breakfast",
            "ingredients": [
                {"id": "i1", "name": "Oats", "amount": 100, "kcal": 389, "protein": 16.9, "fat": 6.9, "carbs": 66.3},
            ],
        }
    )

    assert legacy.inputMethod is None
    assert legacy.aiMeta is None
    assert legacy.dayKey is None
    assert legacy.source is None
    assert legacy.syncState is None
    assert legacy.totals is None
    assert legacy.cloudId is None


def test_legacy_meal_item_defaults_are_safe() -> None:
    """MealItem with only required fields has safe defaults for all optional fields."""
    item = MealItem.model_validate(
        {
            "userUid": "user-1",
            "mealId": "legacy-1",
            "timestamp": "2025-12-01T08:00:00.000Z",
            "type": "breakfast",
            "ingredients": [],
            "createdAt": "2025-12-01T08:00:00.000Z",
            "updatedAt": "2025-12-01T08:00:00.000Z",
            "cloudId": "legacy-1",
        }
    )

    assert item.inputMethod is None
    assert item.aiMeta is None
    assert item.dayKey is None
    assert item.source is None
    assert item.syncState == "synced"
    assert item.notes is None
    assert item.imageId is None
    assert item.photoUrl is None
    assert item.tags == []
    assert item.deleted is False
    assert item.totals.kcal == 0
    assert item.totals.protein == 0


# ---------------------------------------------------------------------------
# Individual field validation
# ---------------------------------------------------------------------------


def test_meal_type_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError):
        MealUpsertRequest.model_validate(
            {
                "mealId": "m1",
                "timestamp": "2026-03-18T12:00:00.000Z",
                "type": "brunch",
                "ingredients": [],
            }
        )


def test_meal_source_accepts_valid_values() -> None:
    for source in ("ai", "manual", "saved", None):
        req = MealUpsertRequest.model_validate(
            {
                "mealId": "m1",
                "timestamp": "2026-03-18T12:00:00.000Z",
                "type": "lunch",
                "ingredients": [],
                "source": source,
            }
        )
        assert req.source == source


def test_all_input_methods_accepted() -> None:
    for method in ("manual", "photo", "barcode", "text", "saved", "quick_add"):
        req = MealUpsertRequest.model_validate(
            {
                "mealId": "m1",
                "timestamp": "2026-03-18T12:00:00.000Z",
                "type": "lunch",
                "ingredients": [],
                "inputMethod": method,
            }
        )
        assert req.inputMethod == method


def test_ai_meta_all_fields_optional() -> None:
    meta = MealAiMeta.model_validate({})
    assert meta.model is None
    assert meta.runId is None
    assert meta.confidence is None
    assert meta.warnings == []


def test_totals_defaults_to_zero() -> None:
    totals = MealTotals.model_validate({})
    assert totals.kcal == 0
    assert totals.protein == 0
    assert totals.fat == 0
    assert totals.carbs == 0


def test_ingredient_unit_accepts_only_g_and_ml() -> None:
    from app.schemas.meal import MealIngredient

    for unit in ("g", "ml", None):
        ing = MealIngredient.model_validate(
            {"id": "i1", "name": "Test", "amount": 100, "unit": unit}
        )
        assert ing.unit == unit

    with pytest.raises(ValidationError):
        MealIngredient.model_validate(
            {"id": "i1", "name": "Test", "amount": 100, "unit": "oz"}
        )
