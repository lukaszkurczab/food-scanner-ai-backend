from pydantic import BaseModel, Field


class FeedbackDeviceInfo(BaseModel):
    modelName: str | None = None
    osName: str | None = None
    osVersion: str | None = None


class FeedbackItem(BaseModel):
    id: str
    message: str = Field(min_length=1, max_length=500)
    userUid: str
    email: str | None = None
    deviceInfo: FeedbackDeviceInfo | None = None
    createdAt: int = Field(ge=0)
    updatedAt: int | None = Field(default=None, ge=0)
    status: str = Field(min_length=1)
    attachmentUrl: str | None = None
    attachmentPath: str | None = None


class FeedbackCreateResponse(BaseModel):
    feedback: FeedbackItem
    created: bool
