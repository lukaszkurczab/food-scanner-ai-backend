"""Schemas for telemetry batch ingestion under the v2 API."""

from __future__ import annotations

import json
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
    }
)

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
