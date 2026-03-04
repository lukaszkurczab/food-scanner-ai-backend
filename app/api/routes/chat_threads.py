from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import AuthenticatedUser, get_required_authenticated_user
from app.core.exceptions import FirestoreServiceError
from app.schemas.chat_thread import (
    ChatMessagePersistRequest,
    ChatMessagePersistResponse,
    ChatMessagesPageResponse,
    ChatThreadsPageResponse,
)
from app.services import chat_thread_service

router = APIRouter()


@router.get("/users/me/chat/threads", response_model=ChatThreadsPageResponse)
async def get_chat_threads_me(
    limit: int = Query(default=20, ge=1, le=100),
    beforeUpdatedAt: int | None = Query(default=None, ge=0),
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> ChatThreadsPageResponse:
    try:
        items, next_before_updated_at = await chat_thread_service.list_threads(
            current_user.uid,
            limit_count=limit,
            before_updated_at=beforeUpdatedAt,
        )
    except FirestoreServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error",
        ) from exc

    return ChatThreadsPageResponse(
        items=items,
        nextBeforeUpdatedAt=next_before_updated_at,
    )


@router.get(
    "/users/me/chat/threads/{threadId}/messages",
    response_model=ChatMessagesPageResponse,
)
async def get_chat_thread_messages_me(
    threadId: str,
    limit: int = Query(default=50, ge=1, le=200),
    beforeCreatedAt: int | None = Query(default=None, ge=0),
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> ChatMessagesPageResponse:
    try:
        items, next_before_created_at = await chat_thread_service.list_messages(
            current_user.uid,
            threadId,
            limit_count=limit,
            before_created_at=beforeCreatedAt,
        )
    except FirestoreServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error",
        ) from exc

    return ChatMessagesPageResponse(
        items=items,
        nextBeforeCreatedAt=next_before_created_at,
    )


@router.post(
    "/users/me/chat/threads/{threadId}/messages",
    response_model=ChatMessagePersistResponse,
)
async def persist_chat_thread_message_me(
    threadId: str,
    request: ChatMessagePersistRequest,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> ChatMessagePersistResponse:
    try:
        await chat_thread_service.persist_message(
            current_user.uid,
            threadId,
            message_id=request.messageId,
            role=request.role,
            content=request.content,
            created_at=request.createdAt,
            title=request.title,
        )
    except FirestoreServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error",
        ) from exc

    return ChatMessagePersistResponse(
        threadId=threadId,
        messageId=request.messageId,
        updated=True,
    )
