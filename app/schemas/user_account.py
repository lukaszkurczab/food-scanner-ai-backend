from typing import Any

from pydantic import BaseModel, Field


class EmailPendingRequest(BaseModel):
    email: str = Field(min_length=3)


class EmailPendingResponse(BaseModel):
    emailPending: str
    updated: bool


class DeleteAccountResponse(BaseModel):
    deleted: bool


class AvatarMetadataRequest(BaseModel):
    avatarUrl: str = Field(min_length=1)


class AvatarMetadataResponse(BaseModel):
    avatarUrl: str
    avatarlastSyncedAt: str
    updated: bool


class UserExportResponse(BaseModel):
    profile: dict[str, Any] | None
    meals: list[dict[str, Any]]
    myMeals: list[dict[str, Any]] = Field(default_factory=list)
    chatMessages: list[dict[str, Any]]
    notifications: list[dict[str, Any]] = Field(default_factory=list)
    notificationPrefs: dict[str, Any] = Field(default_factory=dict)
    feedback: list[dict[str, Any]] = Field(default_factory=list)


class UserProfileResponse(BaseModel):
    profile: dict[str, Any] | None


class UserProfileUpdateResponse(UserProfileResponse):
    updated: bool
