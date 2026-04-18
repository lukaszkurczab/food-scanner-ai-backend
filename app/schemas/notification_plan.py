from typing import Literal

from pydantic import BaseModel, Field


class NotificationPlanRequest(BaseModel):
    startIso: str = Field(min_length=1)
    endIso: str = Field(min_length=1)


class NotificationTime(BaseModel):
    hour: int = Field(ge=0, le=23)
    minute: int = Field(ge=0, le=59)


def _notification_plan_days_default() -> list[int]:
    return []


def _notification_plans_default() -> list["NotificationPlanItem"]:
    return []


class NotificationPlanItem(BaseModel):
    id: str
    type: Literal["meal_reminder", "calorie_goal", "day_fill"]
    enabled: bool
    text: str | None = None
    time: NotificationTime
    days: list[int] = Field(default_factory=_notification_plan_days_default)
    mealKind: Literal["breakfast", "lunch", "dinner", "snack"] | None = None
    shouldSchedule: bool
    missingKcal: int | None = None


class NotificationPlanResponse(BaseModel):
    aiStyle: Literal["none", "concise", "friendly", "detailed"]
    plans: list[NotificationPlanItem] = Field(default_factory=_notification_plans_default)
