from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


WeeklyReportStatus = Literal["ready", "insufficient_data", "not_available"]

WeeklyReportInsightType = Literal[
    "consistency",
    "logging_coverage",
    "start_of_day_pattern",
    "day_completion_pattern",
    "weekend_drift",
    "improving_trend",
]

WeeklyReportInsightImportance = Literal["high", "medium", "low"]

WeeklyReportInsightTone = Literal["positive", "neutral", "negative"]

WeeklyReportPriorityType = Literal[
    "maintain_consistency",
    "increase_logging_coverage",
    "stabilize_start_of_day",
    "improve_day_completion",
    "reduce_weekend_drift",
]


class WeeklyReportPeriod(BaseModel):
    model_config = ConfigDict(extra="forbid")

    startDay: str = Field(min_length=10, max_length=10)
    endDay: str = Field(min_length=10, max_length=10)

    @field_validator("startDay", "endDay")
    @classmethod
    def validate_day_key(cls, value: str) -> str:
        try:
            return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
        except ValueError as exc:
            raise ValueError("period days must use YYYY-MM-DD format") from exc


class WeeklyReportInsight(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: WeeklyReportInsightType
    importance: WeeklyReportInsightImportance
    tone: WeeklyReportInsightTone
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=240)
    reasonCodes: list[str] = Field(default_factory=list, max_length=6)


class WeeklyReportPriority(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: WeeklyReportPriorityType
    text: str = Field(min_length=1, max_length=140)
    reasonCodes: list[str] = Field(default_factory=list, max_length=6)


def _weekly_report_insights_default() -> list[WeeklyReportInsight]:
    return []


def _weekly_report_priorities_default() -> list[WeeklyReportPriority]:
    return []


class WeeklyReportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: WeeklyReportStatus
    period: WeeklyReportPeriod
    summary: str | None = Field(default=None, min_length=1, max_length=160)
    insights: list[WeeklyReportInsight] = Field(default_factory=_weekly_report_insights_default, max_length=4)
    priorities: list[WeeklyReportPriority] = Field(default_factory=_weekly_report_priorities_default, max_length=2)
