from typing import Literal

from pydantic import BaseModel, Field


class NotificationTime(BaseModel):
    hour: int = Field(ge=0, le=23)
    minute: int = Field(ge=0, le=59)


class UserNotificationItem(BaseModel):
    id: str = Field(min_length=1)
    type: Literal["meal_reminder", "calorie_goal", "day_fill"]
    name: str = Field(min_length=1)
    text: str | None = None
    time: NotificationTime
    days: list[int] = Field(default_factory=list)
    enabled: bool
    createdAt: int = Field(ge=0)
    updatedAt: int = Field(ge=0)
    mealKind: Literal["breakfast", "lunch", "dinner", "snack"] | None = None
    kcalByHour: float | None = None


class NotificationListResponse(BaseModel):
    items: list[UserNotificationItem] = Field(default_factory=list)


class NotificationUpsertResponse(BaseModel):
    item: UserNotificationItem
    updated: bool


class NotificationDeleteResponse(BaseModel):
    notificationId: str
    deleted: bool


class NotificationQuietHours(BaseModel):
    startHour: int = Field(ge=0, le=23)
    endHour: int = Field(ge=0, le=23)


class NotificationPrefsPayload(BaseModel):
    motivationEnabled: bool | None = None
    statsEnabled: bool | None = None
    weekdays0to6: list[int] | None = None
    daysAhead: int | None = Field(default=None, ge=1, le=14)
    quietHours: NotificationQuietHours | None = None


class NotificationPrefsResponse(BaseModel):
    notifications: NotificationPrefsPayload


class NotificationPrefsUpdateRequest(BaseModel):
    notifications: NotificationPrefsPayload


class NotificationPrefsUpdateResponse(NotificationPrefsResponse):
    updated: bool
