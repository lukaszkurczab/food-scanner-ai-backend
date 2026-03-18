from typing import Literal

from pydantic import BaseModel, Field


TopRisk = Literal[
    "none",
    "under_logging",
    "low_protein_consistency",
    "high_unknown_meal_details",
    "calorie_under_target",
]

CoachPriority = Literal[
    "maintain",
    "logging_foundation",
    "protein_consistency",
    "meal_detail_quality",
    "calorie_adherence",
]


class HabitWindowDays(BaseModel):
    recentActivity: int = 7
    adherence: int = 14
    consistency: int = 28


class MealTypeCoverage14(BaseModel):
    breakfast: bool = False
    lunch: bool = False
    dinner: bool = False
    snack: bool = False
    other: bool = False
    coveredCount: int = Field(default=0, ge=0, le=5)


class ProteinDaysHit14(BaseModel):
    hitDays: int = Field(default=0, ge=0)
    eligibleDays: int = Field(default=0, ge=0)
    unknownDays: int = Field(default=0, ge=0)
    ratio: float | None = Field(default=None, ge=0, le=1)


class HabitBehavior(BaseModel):
    loggingDays7: int = Field(ge=0, le=7)
    loggingConsistency28: float = Field(ge=0, le=1)
    avgMealsPerLoggedDay14: float = Field(ge=0)
    mealTypeCoverage14: MealTypeCoverage14
    kcalAdherence14: float | None = Field(default=None, ge=0)
    kcalUnderTargetRatio14: float | None = Field(default=None, ge=0, le=1)
    proteinDaysHit14: ProteinDaysHit14


class HabitDataQuality(BaseModel):
    daysWithUnknownMealDetails14: int = Field(default=0, ge=0, le=14)


class HabitSignalsResponse(BaseModel):
    computedAt: str
    windowDays: HabitWindowDays = Field(default_factory=HabitWindowDays)
    behavior: HabitBehavior
    dataQuality: HabitDataQuality
    topRisk: TopRisk
    coachPriority: CoachPriority
