"""Schemas for telemetry batch ingestion under the v2 API."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_SESSION_ID_LENGTH = 128
MAX_EVENT_ID_LENGTH = 128
MAX_EVENT_NAME_LENGTH = 64
MAX_PLATFORM_LENGTH = 32
MAX_APP_VERSION_LENGTH = 40
MAX_BUILD_LENGTH = 40
MAX_LOCALE_LENGTH = 35
MAX_PROP_KEY_LENGTH = 40
MAX_PROP_STRING_LENGTH = 200
MAX_PROPS_JSON_LENGTH = 2_048
MAX_BATCH_SIZE = 50

ALLOWED_TELEMETRY_EVENT_NAMES = frozenset(
    {
        "session_start",
        "session_end",
        "screen_view",
        "meal_add_method_selected",
        "meal_added",
        "meal_updated",
        "meal_deleted",
        "ai_chat_send",
        "ai_chat_result",
        "notification_scheduled",
        "notification_fired",
        "notification_opened",
        "coach_card_viewed",
        "coach_card_expanded",
        "coach_card_cta_clicked",
        "coach_empty_state_viewed",
        "smart_reminder_suppressed",
        "smart_reminder_scheduled",
        "smart_reminder_noop",
        "smart_reminder_decision_failed",
        "smart_reminder_schedule_failed",
    }
)

SMART_REMINDER_CONFIDENCE_BUCKETS = frozenset({"low", "medium", "high"})
SMART_REMINDER_SCHEDULED_WINDOWS = frozenset(
    {"overnight", "morning", "afternoon", "evening", "late_evening"}
)
SMART_REMINDER_KINDS = frozenset(
    {"log_first_meal", "log_next_meal", "complete_day"}
)
SMART_REMINDER_SUPPRESSION_REASONS = frozenset(
    {
        "reminders_disabled",
        "quiet_hours",
        "already_logged_recently",
        "recent_activity_detected",
        "frequency_cap_reached",
    }
)
SMART_REMINDER_NOOP_REASONS = frozenset(
    {"insufficient_signal", "day_already_complete"}
)
SMART_REMINDER_DECISION_FAILURE_REASONS = frozenset(
    {"invalid_payload", "service_unavailable"}
)
SMART_REMINDER_SCHEDULE_FAILURE_REASONS = frozenset(
    {"permission_unavailable", "invalid_time", "schedule_error"}
)

DISALLOWED_TELEMETRY_PROP_KEY_PATTERN = re.compile(
    r"(message|content|email|name|phone)",
    re.IGNORECASE,
)

ALLOWED_TELEMETRY_EVENT_PROPS: dict[str, frozenset[str]] = {
    "session_start": frozenset({"origin"}),
    "session_end": frozenset({"origin", "durationSec", "endReason"}),
    "screen_view": frozenset({"screen"}),
    "meal_add_method_selected": frozenset({"mealInputMethod"}),
    "meal_added": frozenset({"mealInputMethod", "ingredientCount"}),
    "meal_updated": frozenset({"mealInputMethod", "ingredientCount"}),
    "meal_deleted": frozenset({"mealInputMethod"}),
    "ai_chat_send": frozenset({"surface", "chars"}),
    "ai_chat_result": frozenset({"surface", "success", "resultStatus"}),
    "notification_scheduled": frozenset({"notificationType", "origin"}),
    "notification_fired": frozenset({"notificationType", "origin", "foreground"}),
    "notification_opened": frozenset(
        {"notificationType", "origin", "openedFromBackground", "actionIdentifier"}
    ),
    "coach_card_viewed": frozenset({"insightType", "actionType", "isPositive"}),
    "coach_card_expanded": frozenset({"insightType"}),
    "coach_card_cta_clicked": frozenset(
        {"insightType", "actionType", "targetScreen"}
    ),
    "coach_empty_state_viewed": frozenset({"emptyReason"}),
    "smart_reminder_suppressed": frozenset(
        {"decision", "suppressionReason", "confidenceBucket"}
    ),
    "smart_reminder_scheduled": frozenset(
        {"reminderKind", "decision", "confidenceBucket", "scheduledWindow"}
    ),
    "smart_reminder_noop": frozenset(
        {"decision", "noopReason", "confidenceBucket"}
    ),
    "smart_reminder_decision_failed": frozenset({"failureReason"}),
    "smart_reminder_schedule_failed": frozenset(
        {"reminderKind", "decision", "confidenceBucket", "failureReason"}
    ),
}

ALLOWED_TELEMETRY_EVENT_PROP_ENUM_VALUES: dict[
    str, dict[str, frozenset[str]]
] = {
    "smart_reminder_suppressed": {
        "decision": frozenset({"suppress"}),
        "suppressionReason": SMART_REMINDER_SUPPRESSION_REASONS,
        "confidenceBucket": SMART_REMINDER_CONFIDENCE_BUCKETS,
    },
    "smart_reminder_scheduled": {
        "reminderKind": SMART_REMINDER_KINDS,
        "decision": frozenset({"send"}),
        "confidenceBucket": SMART_REMINDER_CONFIDENCE_BUCKETS,
        "scheduledWindow": SMART_REMINDER_SCHEDULED_WINDOWS,
    },
    "smart_reminder_noop": {
        "decision": frozenset({"noop"}),
        "noopReason": SMART_REMINDER_NOOP_REASONS,
        "confidenceBucket": SMART_REMINDER_CONFIDENCE_BUCKETS,
    },
    "smart_reminder_decision_failed": {
        "failureReason": SMART_REMINDER_DECISION_FAILURE_REASONS,
    },
    "smart_reminder_schedule_failed": {
        "reminderKind": SMART_REMINDER_KINDS,
        "decision": frozenset({"send"}),
        "confidenceBucket": SMART_REMINDER_CONFIDENCE_BUCKETS,
        "failureReason": SMART_REMINDER_SCHEDULE_FAILURE_REASONS,
    },
}

def _is_allowed_prop_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, bool):
        return True
    if isinstance(value, str | int | float):
        return True
    if isinstance(value, list):
        if len(value) > 10:
            return False
        return all(_is_allowed_prop_value(item) and not isinstance(item, list | dict) for item in value)
    return False


class TelemetryAppContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform: str = Field(min_length=1, max_length=MAX_PLATFORM_LENGTH)
    appVersion: str = Field(min_length=1, max_length=MAX_APP_VERSION_LENGTH)
    build: str | None = Field(default=None, max_length=MAX_BUILD_LENGTH)

    @field_validator("platform", "appVersion", mode="before")
    @classmethod
    def normalize_required_strings(cls, value: str) -> str:
        return value.strip()

    @field_validator("build", mode="before")
    @classmethod
    def normalize_build(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()


class TelemetryDeviceContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    locale: str | None = Field(default=None, max_length=MAX_LOCALE_LENGTH)
    tzOffsetMin: int | None = Field(default=None, ge=-840, le=840)

    @field_validator("locale", mode="before")
    @classmethod
    def normalize_locale(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()


class TelemetryEventInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eventId: str = Field(min_length=1, max_length=MAX_EVENT_ID_LENGTH)
    name: str = Field(min_length=1, max_length=MAX_EVENT_NAME_LENGTH)
    ts: datetime
    props: dict[str, Any] | None = None

    @field_validator("eventId", "name", mode="before")
    @classmethod
    def normalize_required_strings(cls, value: str) -> str:
        return value.strip()

    @field_validator("props")
    @classmethod
    def validate_props(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None

        for key, prop_value in value.items():
            if len(key) > MAX_PROP_KEY_LENGTH:
                raise ValueError("Telemetry property key is too long")
            if not _is_allowed_prop_value(prop_value):
                raise ValueError("Telemetry property value type is not allowed")
            if isinstance(prop_value, str) and len(prop_value) > MAX_PROP_STRING_LENGTH:
                raise ValueError("Telemetry property value is too long")
            if isinstance(prop_value, list):
                for item in prop_value:
                    if isinstance(item, str) and len(item) > MAX_PROP_STRING_LENGTH:
                        raise ValueError("Telemetry property array value is too long")

        serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
        if len(serialized.encode("utf-8")) > MAX_PROPS_JSON_LENGTH:
            raise ValueError("Telemetry props payload is too large")

        return value

    @model_validator(mode="after")
    def validate_props_contract(self) -> "TelemetryEventInput":
        if self.name not in ALLOWED_TELEMETRY_EVENT_NAMES:
            return self

        props = self.props or {}
        allowed_props = ALLOWED_TELEMETRY_EVENT_PROPS.get(self.name, frozenset())
        for key in props:
            if DISALLOWED_TELEMETRY_PROP_KEY_PATTERN.search(key):
                raise ValueError("Telemetry property key is privacy-sensitive")
            if key not in allowed_props:
                raise ValueError(
                    f"Telemetry property '{key}' is not allowed for event '{self.name}'"
                )

        allowed_enum_values = ALLOWED_TELEMETRY_EVENT_PROP_ENUM_VALUES.get(self.name, {})
        for key, allowed_values in allowed_enum_values.items():
            if key not in props:
                continue
            value = props[key]
            if not isinstance(value, str) or value not in allowed_values:
                raise ValueError(
                    f"Telemetry property '{key}' has invalid value for event '{self.name}'"
                )

        return self


class TelemetryBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sessionId: str = Field(min_length=1, max_length=MAX_SESSION_ID_LENGTH)
    app: TelemetryAppContext
    device: TelemetryDeviceContext
    events: list[TelemetryEventInput] = Field(min_length=1, max_length=MAX_BATCH_SIZE)

    @field_validator("sessionId", mode="before")
    @classmethod
    def normalize_session_id(cls, value: str) -> str:
        return value.strip()


class RejectedTelemetryEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eventId: str
    name: str
    reason: str


class TelemetryBatchIngestResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    acceptedCount: int
    duplicateCount: int
    rejectedCount: int
    rejectedEvents: list[RejectedTelemetryEvent] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_counts(self) -> "TelemetryBatchIngestResponse":
        if self.rejectedCount != len(self.rejectedEvents):
            raise ValueError("Rejected event count does not match rejected event list")
        return self


class TelemetrySummaryEventCount(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    count: int = Field(ge=0)


class TelemetryDailySummaryBucket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day: str
    totalEvents: int = Field(ge=0)
    eventCounts: list[TelemetrySummaryEventCount] = Field(default_factory=list)


class TelemetryDailySummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generatedAt: str
    days: int = Field(ge=1, le=30)
    buckets: list[TelemetryDailySummaryBucket] = Field(default_factory=list)
