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
from pydantic import ValidationError

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
from app.schemas.reminders import (
    NOOP_REASON_CODES,
    SEND_REASON_CODES,
    SUPPRESS_REASON_CODES,
    ReminderDecision,
    ReminderDecisionType,
    ReminderKind,
    ReminderReasonCode,
)
from app.schemas.telemetry import (
    ALLOWED_TELEMETRY_EVENT_NAMES,
    ALLOWED_TELEMETRY_EVENT_PROPS,
)
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
# Fixture: reminder_decision.json
# ---------------------------------------------------------------------------


class TestReminderDecisionContract:
    """Canonical reminder decision fixtures must parse through ReminderDecision."""

    @pytest.fixture()
    def send_fixture(self) -> JSONDict:
        return _load_fixture("reminder_decision.json")

    @pytest.fixture()
    def suppress_fixture(self) -> JSONDict:
        return _load_fixture("reminder_decision_suppress.json")

    @pytest.fixture()
    def noop_fixture(self) -> JSONDict:
        return _load_fixture("reminder_decision_noop.json")

    def test_send_response_parses(self, send_fixture: JSONDict) -> None:
        decision = ReminderDecision.model_validate(send_fixture)
        assert decision.dayKey == "2026-03-18"
        assert decision.computedAt == "2026-03-18T12:00:00Z"
        assert decision.decision == "send"
        assert decision.kind == "log_next_meal"
        assert decision.reasonCodes == [
            "preferred_window_today",
            "day_partially_logged",
        ]
        assert decision.scheduledAtUtc == "2026-03-18T18:30:00Z"
        assert decision.confidence == 0.84
        assert decision.validUntil == "2026-03-18T19:30:00Z"

    def test_suppress_response_parses(self, suppress_fixture: JSONDict) -> None:
        decision = ReminderDecision.model_validate(suppress_fixture)
        assert decision.decision == "suppress"
        assert decision.kind is None
        assert decision.scheduledAtUtc is None
        assert decision.reasonCodes == ["quiet_hours"]
        assert decision.confidence == 1.0

    def test_noop_response_parses(self, noop_fixture: JSONDict) -> None:
        decision = ReminderDecision.model_validate(noop_fixture)
        assert decision.decision == "noop"
        assert decision.kind is None
        assert decision.scheduledAtUtc is None
        assert decision.reasonCodes == ["insufficient_signal"]
        assert decision.confidence == 0.65

    @pytest.mark.parametrize(
        "fixture_name",
        [
            "reminder_decision.json",
            "reminder_decision_suppress.json",
            "reminder_decision_noop.json",
        ],
    )
    def test_fixture_round_trips_through_serialization(self, fixture_name: str) -> None:
        fixture = _load_fixture(fixture_name)
        decision = ReminderDecision.model_validate(fixture)
        serialized = decision.model_dump(mode="json")
        reparsed = ReminderDecision.model_validate(serialized)
        assert reparsed == decision

    @pytest.mark.parametrize(
        "fixture_name",
        [
            "reminder_decision.json",
            "reminder_decision_suppress.json",
            "reminder_decision_noop.json",
        ],
    )
    def test_fixture_top_level_keys_match_schema(self, fixture_name: str) -> None:
        fixture = _load_fixture(fixture_name)
        expected_keys = set(ReminderDecision.model_fields.keys())
        actual_keys = set(fixture.keys())
        assert actual_keys == expected_keys, (
            f"Fixture keys drift. "
            f"Missing from fixture: {expected_keys - actual_keys}. "
            f"Extra in fixture: {actual_keys - expected_keys}."
        )

    def test_send_requires_kind_and_schedule(self) -> None:
        with pytest.raises(ValidationError):
            ReminderDecision.model_validate(
                {
                    "dayKey": "2026-03-18",
                    "computedAt": "2026-03-18T12:00:00Z",
                    "decision": "send",
                    "reasonCodes": ["preferred_window_open"],
                    "confidence": 0.84,
                    "validUntil": "2026-03-18T19:30:00Z",
                }
            )

    def test_noop_rejects_kind_and_schedule(self) -> None:
        with pytest.raises(ValidationError):
            ReminderDecision.model_validate(
                {
                    "dayKey": "2026-03-18",
                    "computedAt": "2026-03-18T12:00:00Z",
                    "decision": "noop",
                    "kind": "complete_day",
                    "reasonCodes": ["insufficient_signal"],
                    "scheduledAtUtc": "2026-03-18T20:00:00Z",
                    "confidence": 0.6,
                    "validUntil": "2026-03-18T23:59:59Z",
                }
            )

    def test_suppress_rejects_kind(self) -> None:
        with pytest.raises(ValidationError):
            ReminderDecision.model_validate(
                {
                    "dayKey": "2026-03-18",
                    "computedAt": "2026-03-18T12:00:00Z",
                    "decision": "suppress",
                    "kind": "log_next_meal",
                    "reasonCodes": ["quiet_hours"],
                    "confidence": 1.0,
                    "validUntil": "2026-03-18T23:59:59Z",
                }
            )

    @pytest.mark.parametrize(
        ("field_name", "value"),
        [
            ("dayKey", "2026/03/18"),
            ("computedAt", "2026-03-18T12:00:00+00:00"),
            ("scheduledAtUtc", "2026-03-18T18:30:00+00:00"),
            ("validUntil", "2026-03-18T19:30:00.000Z"),
        ],
    )
    def test_rejects_non_canonical_date_time_formats(
        self,
        field_name: str,
        value: str,
    ) -> None:
        payload = {
            "dayKey": "2026-03-18",
            "computedAt": "2026-03-18T12:00:00Z",
            "decision": "send",
            "kind": "log_next_meal",
            "reasonCodes": ["preferred_window_open"],
            "scheduledAtUtc": "2026-03-18T18:30:00Z",
            "confidence": 0.84,
            "validUntil": "2026-03-18T19:30:00Z",
        }
        payload[field_name] = value

        with pytest.raises(ValidationError):
            ReminderDecision.model_validate(payload)

    def test_rejects_scheduled_at_utc_earlier_than_computed_at(self) -> None:
        with pytest.raises(ValidationError):
            ReminderDecision.model_validate(
                {
                    "dayKey": "2026-03-18",
                    "computedAt": "2026-03-18T12:00:00Z",
                    "decision": "send",
                    "kind": "log_next_meal",
                    "reasonCodes": ["preferred_window_open"],
                    "scheduledAtUtc": "2026-03-18T11:59:59Z",
                    "confidence": 0.84,
                    "validUntil": "2026-03-18T19:30:00Z",
                }
            )

    def test_rejects_scheduled_at_utc_later_than_valid_until(self) -> None:
        with pytest.raises(ValidationError):
            ReminderDecision.model_validate(
                {
                    "dayKey": "2026-03-18",
                    "computedAt": "2026-03-18T12:00:00Z",
                    "decision": "send",
                    "kind": "log_next_meal",
                    "reasonCodes": ["preferred_window_open"],
                    "scheduledAtUtc": "2026-03-18T19:30:01Z",
                    "confidence": 0.84,
                    "validUntil": "2026-03-18T19:30:00Z",
                }
            )

    def test_rejects_valid_until_earlier_than_computed_at(self) -> None:
        with pytest.raises(ValidationError):
            ReminderDecision.model_validate(
                {
                    "dayKey": "2026-03-18",
                    "computedAt": "2026-03-18T12:00:00Z",
                    "decision": "noop",
                    "reasonCodes": ["insufficient_signal"],
                    "confidence": 0.65,
                    "validUntil": "2026-03-18T11:59:59Z",
                }
            )

    @pytest.mark.parametrize(
        ("decision_type", "reason_codes"),
        [
            ("send", ["quiet_hours"]),
            ("suppress", ["preferred_window_open"]),
            ("noop", ["already_logged_recently"]),
        ],
    )
    def test_rejects_reason_codes_not_allowed_for_decision(
        self,
        decision_type: str,
        reason_codes: list[str],
    ) -> None:
        payload = {
            "dayKey": "2026-03-18",
            "computedAt": "2026-03-18T12:00:00Z",
            "decision": decision_type,
            "kind": "log_next_meal" if decision_type == "send" else None,
            "reasonCodes": reason_codes,
            "scheduledAtUtc": "2026-03-18T18:30:00Z" if decision_type == "send" else None,
            "confidence": 0.84 if decision_type == "send" else 1.0,
            "validUntil": "2026-03-18T19:30:00Z",
        }

        with pytest.raises(ValidationError):
            ReminderDecision.model_validate(payload)


# ---------------------------------------------------------------------------
# Fixture: smart_reminder_telemetry.json
# ---------------------------------------------------------------------------


class TestSmartReminderTelemetryContract:
    @pytest.fixture()
    def fixture(self) -> JSONDict:
        return _load_fixture("smart_reminder_telemetry.json")

    def test_event_names_match_backend_allowlist(self, fixture: JSONDict) -> None:
        expected = {
            "smart_reminder_suppressed",
            "smart_reminder_scheduled",
            "smart_reminder_noop",
            "smart_reminder_decision_failed",
            "smart_reminder_schedule_failed",
        }
        assert set(fixture["eventNames"]) == expected
        assert expected.issubset(ALLOWED_TELEMETRY_EVENT_NAMES)

    def test_props_match_backend_allowlist(self, fixture: JSONDict) -> None:
        expected = {
            "smart_reminder_suppressed": {
                "decision",
                "suppressionReason",
                "confidenceBucket",
            },
            "smart_reminder_scheduled": {
                "reminderKind",
                "decision",
                "confidenceBucket",
                "scheduledWindow",
            },
            "smart_reminder_noop": {
                "decision",
                "noopReason",
                "confidenceBucket",
            },
            "smart_reminder_decision_failed": {
                "failureReason",
            },
            "smart_reminder_schedule_failed": {
                "reminderKind",
                "decision",
                "confidenceBucket",
                "failureReason",
            },
        }
        assert set(fixture["propsByEvent"].keys()) == set(expected.keys())
        for event_name, prop_names in expected.items():
            assert set(fixture["propsByEvent"][event_name]) == prop_names
            assert ALLOWED_TELEMETRY_EVENT_PROPS[event_name] == frozenset(prop_names)

    def test_disallowed_event_names_stay_out_of_allowlist(self, fixture: JSONDict) -> None:
        for event_name in fixture["disallowedEventNames"]:
            assert event_name not in ALLOWED_TELEMETRY_EVENT_NAMES


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

    def test_reminder_decision_type_parity(self, enums: StringListDict) -> None:
        assert sorted(enums["ReminderDecisionType"]) == sorted(
            get_args(ReminderDecisionType)
        )

    def test_reminder_kind_parity(self, enums: StringListDict) -> None:
        assert sorted(enums["ReminderKind"]) == sorted(get_args(ReminderKind))

    def test_reminder_reason_code_parity(self, enums: StringListDict) -> None:
        assert sorted(enums["ReminderReasonCode"]) == sorted(
            get_args(ReminderReasonCode)
        )


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


class TestSmartRemindersContractSnapshotFreshness:
    """Guarantee that the committed snapshot is never stale.

    Re-generates the contract in-memory from the current Python types and
    asserts it matches the committed JSON byte-for-byte.  If this test
    fails, run ``python scripts/export_reminder_contract.py`` and commit
    the updated snapshot.

    This is the canonical freshness gate: backend CI will reject any PR
    where Python types changed but the snapshot was not re-exported.
    """

    def test_committed_snapshot_matches_regenerated_contract(self) -> None:
        import sys

        sys.path.insert(0, str(FIXTURES_DIR.parent.parent / "scripts"))
        from export_reminder_contract import build_contract

        expected = json.dumps(build_contract(), indent=2, ensure_ascii=False) + "\n"
        committed = (FIXTURES_DIR / "smart_reminders_v1.contract.json").read_text(
            encoding="utf-8"
        )
        assert committed == expected, (
            "Committed smart_reminders_v1.contract.json is stale. "
            "Run: python scripts/export_reminder_contract.py"
        )


class TestSmartRemindersContractSnapshot:
    """Validate that backend Python types match the canonical contract snapshot.

    The snapshot at ``smart_reminders_v1.contract.json`` is the cross-repo
    source of truth.  An identical copy lives in the mobile repo.  If this
    test fails, either the snapshot is stale (re-run
    ``scripts/export_reminder_contract.py``) or the types changed
    intentionally and the snapshot needs to be re-exported and synced.
    """

    @pytest.fixture()
    def contract(self) -> JSONDict:
        return _load_fixture("smart_reminders_v1.contract.json")

    def test_decision_types_match_snapshot(self, contract: JSONDict) -> None:
        assert sorted(get_args(ReminderDecisionType)) == sorted(
            contract["decisionTypes"]
        )

    def test_reminder_kinds_match_snapshot(self, contract: JSONDict) -> None:
        assert sorted(get_args(ReminderKind)) == sorted(contract["reminderKinds"])

    def test_all_reason_codes_match_snapshot(self, contract: JSONDict) -> None:
        assert sorted(get_args(ReminderReasonCode)) == sorted(
            contract["reasonCodes"]["all"]
        )

    def test_send_reason_codes_match_snapshot(self, contract: JSONDict) -> None:
        assert sorted(SEND_REASON_CODES) == sorted(contract["reasonCodes"]["send"])

    def test_suppress_reason_codes_match_snapshot(self, contract: JSONDict) -> None:
        assert sorted(SUPPRESS_REASON_CODES) == sorted(
            contract["reasonCodes"]["suppress"]
        )

    def test_noop_reason_codes_match_snapshot(self, contract: JSONDict) -> None:
        assert sorted(NOOP_REASON_CODES) == sorted(contract["reasonCodes"]["noop"])

    def test_telemetry_allowed_events_match_snapshot(self, contract: JSONDict) -> None:
        telemetry = _load_fixture("smart_reminder_telemetry.json")
        assert sorted(telemetry["eventNames"]) == sorted(
            contract["telemetry"]["allowedEvents"]
        )

    def test_telemetry_disallowed_events_match_snapshot(self, contract: JSONDict) -> None:
        telemetry = _load_fixture("smart_reminder_telemetry.json")
        assert sorted(telemetry["disallowedEventNames"]) == sorted(
            contract["telemetry"]["disallowedEvents"]
        )

    def test_telemetry_props_match_snapshot(self, contract: JSONDict) -> None:
        telemetry = _load_fixture("smart_reminder_telemetry.json")
        for event_name, props in telemetry["propsByEvent"].items():
            assert sorted(props) == sorted(
                contract["telemetry"]["propsByEvent"][event_name]
            ), f"Props mismatch for {event_name}"

    def test_decision_shape_required_fields(self, contract: JSONDict) -> None:
        schema_fields = set(ReminderDecision.model_fields.keys())
        snapshot_fields = set(contract["decisionShape"]["requiredFields"])
        assert schema_fields == snapshot_fields

    def test_reason_code_groups_are_exhaustive(self, contract: JSONDict) -> None:
        """send + suppress + noop reason codes must equal all reason codes."""
        grouped = sorted(
            contract["reasonCodes"]["send"]
            + contract["reasonCodes"]["suppress"]
            + contract["reasonCodes"]["noop"]
        )
        assert grouped == sorted(contract["reasonCodes"]["all"])
