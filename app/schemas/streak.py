from pydantic import BaseModel, Field


class StreakResponse(BaseModel):
    current: int
    lastDate: str | None
    awardedBadgeIds: list[str] = Field(default_factory=list)


class StreakWriteRequest(BaseModel):
    dayKey: str = Field(min_length=10, max_length=10)


class StreakRecalculateRequest(BaseModel):
    dayKey: str = Field(min_length=10, max_length=10)
    todaysKcal: float
    targetKcal: float
    thresholdPct: float = Field(default=0.8, gt=0, le=1)
