from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


CoachInsightType = Literal[
    "under_logging",
    "high_unknown_meal_details",
    "low_protein_consistency",
    "calorie_under_target",
    "positive_momentum",
    "stable",
]

CoachActionType = Literal[
    "log_next_meal",
    "open_chat",
    "review_history",
    "none",
]

CoachSource = Literal["rules"]

CoachEmptyReason = Literal["no_data", "insufficient_data"]


class CoachInsight(BaseModel):
    id: str
    type: CoachInsightType
    priority: int = Field(ge=0)
    title: str
    body: str
    actionLabel: str | None = None
    actionType: CoachActionType = "none"
    reasonCodes: list[str] = Field(default_factory=list)
    source: CoachSource = "rules"
    validUntil: str | None = None
    confidence: float = Field(ge=0, le=1)
    isPositive: bool = False


class CoachMeta(BaseModel):
    available: bool = False
    emptyReason: CoachEmptyReason | None = None
    isDegraded: bool = False


def _coach_insights_default() -> list[CoachInsight]:
    return []


class CoachResponse(BaseModel):
    dayKey: str
    computedAt: str
    source: CoachSource = "rules"
    insights: list[CoachInsight] = Field(default_factory=_coach_insights_default, max_length=3)
    topInsight: CoachInsight | None = None
    meta: CoachMeta = Field(default_factory=CoachMeta)
