from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from app.api.deps import AuthenticatedUser, get_required_authenticated_user
from app.core.exceptions import FirestoreServiceError
from app.schemas.user_account import (
    AvatarMetadataRequest,
    AvatarMetadataResponse,
    DeleteAccountResponse,
    EmailPendingRequest,
    EmailPendingResponse,
    UserProfileResponse,
    UserProfileUpdateResponse,
    UserExportResponse,
)
from app.services import user_account_service
from app.services.user_account_service import (
    AvatarMetadataValidationError,
    EmailValidationError,
    UserProfileValidationError,
)

router = APIRouter()


async def _set_email_pending_for_user(
    *,
    user_id: str,
    request: EmailPendingRequest,
) -> EmailPendingResponse:
    try:
        normalized_email = await user_account_service.set_email_pending(
            user_id,
            request.email,
        )
    except EmailValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except FirestoreServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error",
        ) from exc

    return EmailPendingResponse(
        emailPending=normalized_email,
        updated=True,
    )


async def _set_avatar_metadata_for_user(
    *,
    user_id: str,
    request: AvatarMetadataRequest,
) -> AvatarMetadataResponse:
    try:
        normalized_avatar_url, synced_at = await user_account_service.set_avatar_metadata(
            user_id,
            request.avatarUrl,
        )
    except AvatarMetadataValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except FirestoreServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error",
        ) from exc

    return AvatarMetadataResponse(
        avatarUrl=normalized_avatar_url,
        avatarlastSyncedAt=synced_at,
        updated=True,
    )


async def _delete_account_for_user(*, user_id: str) -> DeleteAccountResponse:
    try:
        await user_account_service.delete_account_data(user_id)
    except FirestoreServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error",
        ) from exc

    return DeleteAccountResponse(deleted=True)


async def _get_user_export_for_user(*, user_id: str) -> UserExportResponse:
    try:
        profile, meals, my_meals, chat_messages, notifications, notification_prefs, feedback = (
            await user_account_service.get_user_export_data(user_id)
        )
    except FirestoreServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error",
        ) from exc

    return UserExportResponse(
        profile=profile,
        meals=meals,
        myMeals=my_meals,
        chatMessages=chat_messages,
        notifications=notifications,
        notificationPrefs=notification_prefs,
        feedback=feedback,
    )


async def _get_user_profile_for_user(*, user_id: str) -> UserProfileResponse:
    try:
        profile = await user_account_service.get_user_profile_data(user_id)
    except FirestoreServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error",
        ) from exc

    return UserProfileResponse(profile=profile)


async def _upsert_user_profile_for_user(
    *,
    user_id: str,
    payload: dict[str, object],
    auth_email: str | None,
) -> UserProfileUpdateResponse:
    try:
        profile = await user_account_service.upsert_user_profile_data(
            user_id,
            payload,
            auth_email=auth_email,
        )
    except UserProfileValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except FirestoreServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error",
        ) from exc

    return UserProfileUpdateResponse(profile=profile, updated=True)


@router.get("/users/me/profile", response_model=UserProfileResponse)
async def get_user_profile_me(
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> UserProfileResponse:
    return await _get_user_profile_for_user(user_id=current_user.uid)


@router.post("/users/me/profile", response_model=UserProfileUpdateResponse)
async def upsert_user_profile_me(
    payload: dict[str, object],
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> UserProfileUpdateResponse:
    auth_email = current_user.claims.get("email")
    return await _upsert_user_profile_for_user(
        user_id=current_user.uid,
        payload=payload,
        auth_email=auth_email if isinstance(auth_email, str) else None,
    )


@router.post("/users/me/email-pending", response_model=EmailPendingResponse)
async def set_email_pending_me(
    request: EmailPendingRequest,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> EmailPendingResponse:
    return await _set_email_pending_for_user(user_id=current_user.uid, request=request)


@router.post("/users/me/avatar-metadata", response_model=AvatarMetadataResponse)
async def set_avatar_metadata_me(
    request: AvatarMetadataRequest,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> AvatarMetadataResponse:
    return await _set_avatar_metadata_for_user(user_id=current_user.uid, request=request)


@router.post("/users/me/avatar", response_model=AvatarMetadataResponse)
async def upload_avatar_me(
    file: UploadFile = File(...),
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> AvatarMetadataResponse:
    try:
        normalized_avatar_url, synced_at = await user_account_service.upload_avatar(
            current_user.uid,
            file,
        )
    except FirestoreServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error",
        ) from exc

    return AvatarMetadataResponse(
        avatarUrl=normalized_avatar_url,
        avatarlastSyncedAt=synced_at,
        updated=True,
    )


@router.post("/users/me/delete", response_model=DeleteAccountResponse)
async def delete_account_me(
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> DeleteAccountResponse:
    return await _delete_account_for_user(user_id=current_user.uid)


@router.get("/users/me/export", response_model=UserExportResponse)
async def get_user_export_me(
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> UserExportResponse:
    return await _get_user_export_for_user(user_id=current_user.uid)
