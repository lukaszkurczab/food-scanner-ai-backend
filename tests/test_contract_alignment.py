"""Cross-repo contract alignment tests.

These tests validate that the canonical JSON fixtures in
``tests/contract_fixtures/`` can be parsed by the backend Pydantic models
and that all enum values match the backend's Literal definitions.

Mirror fixtures live in the mobile repo at
``src/__contract_fixtures__/``.  When a fixture changes, the
corresponding test must break in *both* repos to prevent silent drift.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, get_args

import pytest
from pytest_mock import MockerFixture

from app.schemas.coach import (
    CoachMeta,
    CoachActionType,
    CoachEmptyReason,
    CoachInsightType,
    CoachResponse,
    CoachSource,
)
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
from app.services.coach_rule_engine import evaluate_coach_insights, select_top_insight
from app.services.coach_service import get_coach_response

FIXTURES_DIR = Path(__file__).parent / "contract_fixtures"
JSONDict = dict[str, Any]
StringListDict = dict[str, list[str]]


def _load_fixture(name: str) -> JSONDict:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def _load_nutrition_state_fixture_model() -> NutritionStateResponse:
    return NutritionStateResponse.model_validate(_load_fixture("nutrition_state.json"))


def _build_runtime_coach_response_from_state(state: NutritionStateResponse) -> CoachResponse:
    evaluation = evaluate_coach_insights(state)
    return CoachResponse(
        dayKey=state.dayKey,
        computedAt=state.computedAt,
        source="rules",
        insights=evaluation.insights,
        topInsight=select_top_insight(evaluation.insights),
        meta=CoachMeta(
            available=True,
            emptyReason=evaluation.empty_reason,
            isDegraded=state.meta.componentStatus.streak == "error",
        ),
    )


# ---------------------------------------------------------------------------
# Fixture: meal_item.json
# ---------------------------------------------------------------------------


class TestMealItemContract:
    """Canonical meal fixture must parse through both MealItem and MealUpsertRequest."""

    @pytest.fixture()
    def fixture(self) -> JSONDict:
        return _load_fixture("meal_item.json")

    def test_meal_item_parses(self, fixture: JSONDict) -> None:
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
        assert item.loggedAtLocalMin == 780
        assert item.tzOffsetMin == 60
        assert item.deleted is False

    def test_meal_upsert_request_parses(self, fixture: JSONDict) -> None:
        req = MealUpsertRequest.model_validate(fixture)
        assert req.mealId == "meal-contract-1"
        assert req.type == "lunch"
        assert req.totals is not None
        assert req.totals.protein == 62.0

    def test_fixture_round_trips_through_serialization(self, fixture: JSONDict) -> None:
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
    def fixture(self) -> JSONDict:
        return _load_fixture("nutrition_state.json")

    def test_response_parses(self, fixture: JSONDict) -> None:
        state = NutritionStateResponse.model_validate(fixture)
        assert state.dayKey == "2026-03-18"
        assert state.targets.kcal == 2200.0
        assert state.consumed.protein == 98.0
        assert state.remaining.carbs == 90.0
        assert state.overTarget.kcal == 0.0
        assert state.quality.mealsLogged == 3
        assert state.quality.dataCompletenessScore == 1.0

    def test_habits_summary_parses(self, fixture: JSONDict) -> None:
        state = NutritionStateResponse.model_validate(fixture)
        assert state.habits.available is True
        assert state.habits.behavior.loggingDays7 == 5
        assert state.habits.behavior.validLoggingDays7 == 4
        assert state.habits.behavior.loggingConsistency28 == 0.75
        assert state.habits.behavior.validLoggingConsistency28 == 0.61
        assert state.habits.behavior.avgValidMealsPerValidLoggedDay14 == 2.5
        assert state.habits.behavior.mealTypeCoverage14.coveredCount == 3
        assert state.habits.behavior.mealTypeFrequency14.lunch == 5
        assert state.habits.behavior.dayCoverage14.validLoggedDays == 8
        assert state.habits.behavior.proteinDaysHit14.ratio == 0.67
        assert state.habits.behavior.timingPatterns14.available is True
        assert state.habits.behavior.timingPatterns14.firstMealMedianHour == 8.25
        assert state.habits.topRisk == "none"
        assert state.habits.coachPriority == "maintain"
        assert state.habits.dataQuality.daysUsingTimestampTimingFallback14 == 2

    def test_streak_summary_parses(self, fixture: JSONDict) -> None:
        state = NutritionStateResponse.model_validate(fixture)
        assert state.streak.available is True
        assert state.streak.current == 5
        assert state.streak.lastDate == "2026-03-18"

    def test_ai_summary_parses(self, fixture: JSONDict) -> None:
        state = NutritionStateResponse.model_validate(fixture)
        assert state.ai.available is True
        assert state.ai.tier == "free"
        assert state.ai.balance == 85
        assert state.ai.costs.chat == 1
        assert state.ai.costs.photo == 5
        assert state.meta.isDegraded is False
        assert state.meta.componentStatus.habits == "ok"

    def test_fixture_top_level_keys_match_schema(self, fixture: JSONDict) -> None:
        """Fixture must contain exactly the fields NutritionStateResponse declares."""
        expected_keys = set(NutritionStateResponse.model_fields.keys())
        actual_keys = set(fixture.keys())
        assert actual_keys == expected_keys, (
            f"Fixture keys drift. "
            f"Missing from fixture: {expected_keys - actual_keys}. "
            f"Extra in fixture: {actual_keys - expected_keys}."
        )


# ---------------------------------------------------------------------------
# Fixture: coach_response.json
# ---------------------------------------------------------------------------


class TestCoachResponseContract:
    """Canonical coach response fixture must parse through CoachResponse."""

    @pytest.fixture()
    def fixture(self) -> JSONDict:
        return _load_fixture("coach_response.json")

    def test_response_parses(self, fixture: JSONDict) -> None:
        response = CoachResponse.model_validate(fixture)
        assert response.dayKey == "2026-03-18"
        assert response.computedAt == "2026-03-18T12:00:00Z"
        assert response.source == "rules"
        assert len(response.insights) == 1
        assert response.meta.available is True
        assert response.meta.emptyReason is None
        assert response.meta.isDegraded is False

    def test_top_insight_parses(self, fixture: JSONDict) -> None:
        response = CoachResponse.model_validate(fixture)
        assert response.topInsight is not None
        assert response.topInsight.id == "2026-03-18:positive_momentum"
        assert response.topInsight.type == "positive_momentum"
        assert response.topInsight.actionType == "open_chat"
        assert response.topInsight.reasonCodes == [
            "streak_positive",
            "consistency_improving",
        ]
        assert response.topInsight.validUntil == "2026-03-18T23:59:59Z"
        assert response.topInsight.confidence == 0.74
        assert response.topInsight.isPositive is True

    def test_fixture_matches_runtime_rule_engine_output(
        self,
        fixture: JSONDict,
    ) -> None:
        state = _load_nutrition_state_fixture_model()
        evaluation = evaluate_coach_insights(state)
        top_insight = select_top_insight(evaluation.insights)

        assert {
            "insights": [insight.model_dump(mode="json") for insight in evaluation.insights],
            "topInsight": (
                top_insight.model_dump(mode="json") if top_insight is not None else None
            ),
            "meta": {
                "available": True,
                "emptyReason": evaluation.empty_reason,
                "isDegraded": state.meta.componentStatus.streak == "error",
            },
        } == {
            "insights": fixture["insights"],
            "topInsight": fixture["topInsight"],
            "meta": fixture["meta"],
        }

    def test_fixture_matches_runtime_coach_response_output(
        self,
        fixture: JSONDict,
        mocker: MockerFixture,
    ) -> None:
        state = _load_nutrition_state_fixture_model()
        mocker.patch(
            "app.services.coach_service.get_nutrition_state",
            return_value=state,
        )

        response = asyncio.run(get_coach_response("user-contract-1", day_key=state.dayKey))

        assert response.model_dump(mode="json") == fixture

    def test_runtime_helper_matches_fixture(self, fixture: JSONDict) -> None:
        state = _load_nutrition_state_fixture_model()
        response = _build_runtime_coach_response_from_state(state)

        assert response.model_dump(mode="json") == fixture

    def test_single_insight_fixture_parses(self, fixture: JSONDict) -> None:
        response = CoachResponse.model_validate(fixture)
        assert response.insights == [response.topInsight]
        assert response.insights[0].id == "2026-03-18:positive_momentum"
        assert response.insights[0].validUntil == "2026-03-18T23:59:59Z"

    def test_fixture_top_level_keys_match_schema(self, fixture: JSONDict) -> None:
        expected_keys = set(CoachResponse.model_fields.keys())
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
    def fixture(self) -> JSONDict:
        return _load_fixture("gateway_reject.json")

    def test_reject_detail_shape(self, fixture: JSONDict) -> None:
        detail = fixture["detail"]
        assert detail["message"] == "AI request blocked by gateway"
        assert detail["code"] == "AI_GATEWAY_BLOCKED"
        assert isinstance(detail["reason"], str)
        assert isinstance(detail["score"], (int, float))

    def test_reject_reason_is_canonical(self, fixture: JSONDict) -> None:
        """The reason in the fixture must be one of the backend's canonical constants."""
        canonical_reasons = {REJECT_REASON_OFF_TOPIC, REJECT_REASON_TOO_SHORT}
        assert fixture["detail"]["reason"] in canonical_reasons


# ---------------------------------------------------------------------------
# Fixture: enums.json — enum value parity
# ---------------------------------------------------------------------------


class TestEnumParity:
    """Enum values in the fixture must exactly match backend Literal definitions."""

    @pytest.fixture()
    def enums(self) -> StringListDict:
        return _load_fixture("enums.json")

    def test_meal_type_parity(self, enums: StringListDict) -> None:
        assert sorted(enums["MealType"]) == sorted(get_args(MealType))

    def test_meal_sync_state_parity(self, enums: StringListDict) -> None:
        assert sorted(enums["MealSyncState"]) == sorted(get_args(MealSyncState))

    def test_meal_input_method_parity(self, enums: StringListDict) -> None:
        assert sorted(enums["MealInputMethod"]) == sorted(get_args(MealInputMethod))

    def test_meal_source_parity(self, enums: StringListDict) -> None:
        # MealSource is Literal["ai", "manual", "saved"] | None — extract the Literal part.
        literal_args = get_args(MealSource)
        # The union is (Literal[...], None); extract from Literal.
        source_values = []
        for arg in literal_args:
            inner = get_args(arg)
            if inner:
                source_values.extend(inner)
        assert sorted(enums["MealSource"]) == sorted(source_values)

    def test_gateway_reject_reasons_parity(self, enums: StringListDict) -> None:
        backend_reasons = {REJECT_REASON_OFF_TOPIC, REJECT_REASON_TOO_SHORT}
        assert sorted(enums["GatewayRejectReasons"]) == sorted(backend_reasons)

    def test_top_risk_parity(self, enums: StringListDict) -> None:
        assert sorted(enums["TopRisk"]) == sorted(get_args(TopRisk))

    def test_coach_priority_parity(self, enums: StringListDict) -> None:
        assert sorted(enums["CoachPriority"]) == sorted(get_args(CoachPriority))

    def test_ai_tier_parity(self, enums: StringListDict) -> None:
        assert sorted(enums["AiTier"]) == sorted(["free", "premium"])


class TestCoachContractEnums:
    """Coach contract Literals must stay aligned with the v1 contract doc."""

    def test_coach_insight_type_values(self) -> None:
        assert sorted(get_args(CoachInsightType)) == sorted(
            [
                "under_logging",
                "high_unknown_meal_details",
                "low_protein_consistency",
                "calorie_under_target",
                "positive_momentum",
                "stable",
            ]
        )

    def test_coach_action_type_values(self) -> None:
        assert sorted(get_args(CoachActionType)) == sorted(
            ["log_next_meal", "open_chat", "review_history", "none"]
        )

    def test_coach_source_values(self) -> None:
        assert sorted(get_args(CoachSource)) == ["rules"]

    def test_coach_empty_reason_values(self) -> None:
        assert sorted(get_args(CoachEmptyReason)) == sorted(
            ["no_data", "insufficient_data"]
        )
