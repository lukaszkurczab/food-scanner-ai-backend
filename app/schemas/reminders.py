from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


ReminderDecisionType = Literal["send", "suppress", "noop"]

ReminderKind = Literal["log_first_meal", "log_next_meal", "complete_day"]

ReminderReasonCode = Literal[
    "preferred_window_open",
    "preferred_window_today",
    "habit_window_match",
    "habit_window_today",
    "day_empty",
    "day_partially_logged",
    "logging_usually_happens_now",
    "recent_activity_detected",
    "already_logged_recently",
    "quiet_hours",
    "reminders_disabled",
    "insufficient_signal",
    "day_already_complete",
]

SEND_REASON_CODES: frozenset[ReminderReasonCode] = frozenset(
    {
        "preferred_window_open",
        "preferred_window_today",
        "habit_window_match",
        "habit_window_today",
        "day_empty",
        "day_partially_logged",
        "logging_usually_happens_now",
    }
)
SUPPRESS_REASON_CODES: frozenset[ReminderReasonCode] = frozenset(
    {
        "recent_activity_detected",
        "already_logged_recently",
        "quiet_hours",
        "reminders_disabled",
    }
)
NOOP_REASON_CODES: frozenset[ReminderReasonCode] = frozenset(
    {
        "insufficient_signal",
        "day_already_complete",
    }
)


class ReminderDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dayKey: str = Field(min_length=10, max_length=10)
    computedAt: str = Field(min_length=20, max_length=20)
    decision: ReminderDecisionType
    kind: ReminderKind | None = None
    reasonCodes: list[ReminderReasonCode] = Field(default_factory=list, min_length=1)
    scheduledAtUtc: str | None = Field(default=None, min_length=20, max_length=20)
    confidence: float = Field(ge=0, le=1)
    validUntil: str = Field(min_length=20, max_length=20)

    @field_validator("dayKey")
    @classmethod
    def validate_day_key(cls, value: str) -> str:
        try:
            return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
        except ValueError as exc:
            raise ValueError("dayKey must use YYYY-MM-DD format") from exc

    @field_validator("computedAt", "validUntil")
    @classmethod
    def validate_utc_timestamp(cls, value: str) -> str:
        try:
            return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=None
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError as exc:
            raise ValueError(
                "timestamps must use canonical UTC format YYYY-MM-DDTHH:MM:SSZ"
            ) from exc

    @field_validator("scheduledAtUtc")
    @classmethod
    def validate_optional_utc_timestamp(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return cls.validate_utc_timestamp(value)

    @model_validator(mode="after")
    def validate_semantics(self) -> ReminderDecision:
        computed_at = datetime.strptime(self.computedAt, "%Y-%m-%dT%H:%M:%SZ")
        valid_until = datetime.strptime(self.validUntil, "%Y-%m-%dT%H:%M:%SZ")

        if valid_until < computed_at:
            raise ValueError("validUntil must not be earlier than computedAt")

        if self.decision == "send":
            if self.kind is None:
                raise ValueError("send decisions must declare a reminder kind")
            if self.scheduledAtUtc is None:
                raise ValueError("send decisions must declare scheduledAtUtc")
            scheduled_at = datetime.strptime(
                self.scheduledAtUtc, "%Y-%m-%dT%H:%M:%SZ"
            )
            if scheduled_at < computed_at:
                raise ValueError("scheduledAtUtc must not be earlier than computedAt")
            if scheduled_at > valid_until:
                raise ValueError("scheduledAtUtc must not be later than validUntil")
            if not set(self.reasonCodes).issubset(SEND_REASON_CODES):
                raise ValueError("send decisions contain unsupported reason codes")
            return self

        if self.scheduledAtUtc is not None:
            raise ValueError("only send decisions may declare scheduledAtUtc")

        if self.kind is not None:
            raise ValueError("only send decisions may declare a reminder kind")

        if self.decision == "suppress":
            if not set(self.reasonCodes).issubset(SUPPRESS_REASON_CODES):
                raise ValueError("suppress decisions contain unsupported reason codes")
            return self

        if not set(self.reasonCodes).issubset(NOOP_REASON_CODES):
            raise ValueError("noop decisions contain unsupported reason codes")

        return self
