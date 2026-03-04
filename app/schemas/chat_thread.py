from typing import Literal

from pydantic import BaseModel, Field


class ChatThreadItem(BaseModel):
    id: str
    title: str
    createdAt: int
    updatedAt: int
    lastMessage: str | None = None
    lastMessageAt: int | None = None


class ChatThreadsPageResponse(BaseModel):
    items: list[ChatThreadItem]
    nextBeforeUpdatedAt: int | None = None


class ChatMessageItem(BaseModel):
    id: str
    role: Literal["user", "assistant", "system"]
    content: str
    createdAt: int
    lastSyncedAt: int
    deleted: bool = False


class ChatMessagesPageResponse(BaseModel):
    items: list[ChatMessageItem]
    nextBeforeCreatedAt: int | None = None


class ChatMessagePersistRequest(BaseModel):
    messageId: str = Field(min_length=1)
    role: Literal["user", "assistant", "system"]
    content: str
    createdAt: int = Field(ge=0)
    title: str | None = None


class ChatMessagePersistResponse(BaseModel):
    threadId: str
    messageId: str
    updated: bool
