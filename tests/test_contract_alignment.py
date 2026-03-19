"""Cross-repo contract alignment tests.

These tests validate that the canonical JSON fixtures in
``tests/contract_fixtures/`` can be parsed by the backend Pydantic models
and that all enum values match the backend's Literal definitions.

Mirror fixtures live in the mobile repo at
``src/__contract_fixtures__/``.  When a fixture changes, the
corresponding test must break in *both* repos to prevent silent drift.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import get_args

import pytest

from app.schemas.habits import CoachPriority, TopRisk
from app.schemas.meal import (
    MealInputMethod,
    MealItem,
    MealSource,
    MealSyncState,
    MealType,
    MealUpsertRequest,
)
from app.schemas.nutrition_state import NutritionStateResponse
from app.services.ai_gateway_service import (
    REJECT_REASON_OFF_TOPIC,
    REJECT_REASON_TOO_SHORT,
)

FIXTURES_DIR = Path(__file__).parent / "contract_fixtures"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Fixture: meal_item.json
# ---------------------------------------------------------------------------


class TestMealItemContract:
    """Canonical meal fixture must parse through both MealItem and MealUpsertRequest."""

    @pytest.fixture()
    def fixture(self) -> dict:
        return _load_fixture("meal_item.json")

    def test_meal_item_parses(self, fixture: dict) -> None:
        item = MealItem.model_validate(fixture)
        assert item.mealId == "meal-contract-1"
        assert item.type == "lunch"
        assert item.syncState == "synced"
        assert item.inputMethod == "photo"
        assert item.source == "ai"
        assert item.totals.kcal == 330.0
        assert len(item.ingredients) == 1
        assert item.ingredients[0].protein == 62.0
        assert item.aiMeta is not None
        assert item.aiMeta.model == "gpt-4o"
        assert item.dayKey == "2026-03-18"
        assert item.deleted is False

    def test_meal_upsert_request_parses(self, fixture: dict) -> None:
        req = MealUpsertRequest.model_validate(fixture)
        assert req.mealId == "meal-contract-1"
        assert req.type == "lunch"
        assert req.totals is not None
        assert req.totals.protein == 62.0

    def test_fixture_round_trips_through_serialization(self, fixture: dict) -> None:
        """Parse → serialize → parse must be stable."""
        item = MealItem.model_validate(fixture)
        serialized = item.model_dump(mode="json")
        reparsed = MealItem.model_validate(serialized)
        assert reparsed.mealId == item.mealId
        assert reparsed.totals.kcal == item.totals.kcal
        assert reparsed.ingredients[0].protein == item.ingredients[0].protein


# ---------------------------------------------------------------------------
# Fixture: nutrition_state.json
# ---------------------------------------------------------------------------


class TestNutritionStateContract:
    """Canonical nutrition state fixture must parse through NutritionStateResponse."""

    @pytest.fixture()
    def fixture(self) -> dict:
        return _load_fixture("nutrition_state.json")

    def test_response_parses(self, fixture: dict) -> None:
        state = NutritionStateResponse.model_validate(fixture)
        assert state.dayKey == "2026-03-18"
        assert state.targets.kcal == 2200.0
        assert state.consumed.protein == 98.0
        assert state.remaining.carbs == 90.0
        assert state.quality.mealsLogged == 3
        assert state.quality.dataCompletenessScore == 1.0

    def test_habits_summary_parses(self, fixture: dict) -> None:
        state = NutritionStateResponse.model_validate(fixture)
        assert state.habits.available is True
        assert state.habits.behavior.loggingDays7 == 5
        assert state.habits.behavior.loggingConsistency28 == 0.75
        assert state.habits.behavior.mealTypeCoverage14.coveredCount == 3
        assert state.habits.behavior.proteinDaysHit14.ratio == 0.67
        assert state.habits.topRisk == "none"
        assert state.habits.coachPriority == "maintain"

    def test_streak_summary_parses(self, fixture: dict) -> None:
        state = NutritionStateResponse.model_validate(fixture)
        assert state.streak.available is True
        assert state.streak.current == 5
        assert state.streak.lastDate == "2026-03-18"

    def test_ai_summary_parses(self, fixture: dict) -> None:
        state = NutritionStateResponse.model_validate(fixture)
        assert state.ai.available is True
        assert state.ai.tier == "free"
        assert state.ai.balance == 85
        assert state.ai.costs.chat == 1
        assert state.ai.costs.photo == 5

    def test_fixture_top_level_keys_match_schema(self, fixture: dict) -> None:
        """Fixture must contain exactly the fields NutritionStateResponse declares."""
        expected_keys = set(NutritionStateResponse.model_fields.keys())
        actual_keys = set(fixture.keys())
        assert actual_keys == expected_keys, (
            f"Fixture keys drift. "
            f"Missing from fixture: {expected_keys - actual_keys}. "
            f"Extra in fixture: {actual_keys - expected_keys}."
        )


# ---------------------------------------------------------------------------
# Fixture: gateway_reject.json
# ---------------------------------------------------------------------------


class TestGatewayRejectContract:
    """Canonical gateway reject fixture matches route HTTP 400 shape."""

    @pytest.fixture()
    def fixture(self) -> dict:
        return _load_fixture("gateway_reject.json")

    def test_reject_detail_shape(self, fixture: dict) -> None:
        detail = fixture["detail"]
        assert detail["message"] == "AI request blocked by gateway"
        assert detail["code"] == "AI_GATEWAY_BLOCKED"
        assert isinstance(detail["reason"], str)
        assert isinstance(detail["score"], (int, float))

    def test_reject_reason_is_canonical(self, fixture: dict) -> None:
        """The reason in the fixture must be one of the backend's canonical constants."""
        canonical_reasons = {REJECT_REASON_OFF_TOPIC, REJECT_REASON_TOO_SHORT}
        assert fixture["detail"]["reason"] in canonical_reasons


# ---------------------------------------------------------------------------
# Fixture: enums.json — enum value parity
# ---------------------------------------------------------------------------


class TestEnumParity:
    """Enum values in the fixture must exactly match backend Literal definitions."""

    @pytest.fixture()
    def enums(self) -> dict:
        return _load_fixture("enums.json")

    def test_meal_type_parity(self, enums: dict) -> None:
        assert sorted(enums["MealType"]) == sorted(get_args(MealType))

    def test_meal_sync_state_parity(self, enums: dict) -> None:
        assert sorted(enums["MealSyncState"]) == sorted(get_args(MealSyncState))

    def test_meal_input_method_parity(self, enums: dict) -> None:
        assert sorted(enums["MealInputMethod"]) == sorted(get_args(MealInputMethod))

    def test_meal_source_parity(self, enums: dict) -> None:
        # MealSource is Literal["ai", "manual", "saved"] | None — extract the Literal part.
        literal_args = get_args(MealSource)
        # The union is (Literal[...], None); extract from Literal.
        source_values = []
        for arg in literal_args:
            inner = get_args(arg)
            if inner:
                source_values.extend(inner)
        assert sorted(enums["MealSource"]) == sorted(source_values)

    def test_gateway_reject_reasons_parity(self, enums: dict) -> None:
        backend_reasons = {REJECT_REASON_OFF_TOPIC, REJECT_REASON_TOO_SHORT}
        assert sorted(enums["GatewayRejectReasons"]) == sorted(backend_reasons)

    def test_top_risk_parity(self, enums: dict) -> None:
        assert sorted(enums["TopRisk"]) == sorted(get_args(TopRisk))

    def test_coach_priority_parity(self, enums: dict) -> None:
        assert sorted(enums["CoachPriority"]) == sorted(get_args(CoachPriority))

    def test_ai_tier_parity(self, enums: dict) -> None:
        assert sorted(enums["AiTier"]) == sorted(["free", "premium"])
