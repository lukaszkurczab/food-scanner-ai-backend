from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from app.api.deps import AuthenticatedUser, get_required_authenticated_user
from app.api.http_errors import raise_bad_request
from app.schemas.user_account import (
    AvatarMetadataRequest,
    AvatarMetadataResponse,
    DeleteAccountResponse,
    EmailPendingRequest,
    EmailPendingResponse,
    UserOnboardingRequest,
    UserOnboardingResponse,
    UserExportResponse,
    UserProfileResponse,
    UserProfileUpdateResponse,
)
from app.services import user_account_service
from app.services.user_account_service import (
    AvatarMetadataValidationError,
    EmailValidationError,
    OnboardingUsernameUnavailableError,
    OnboardingValidationError,
    UserProfileValidationError,
)

router = APIRouter()


@router.get("/users/me/profile", response_model=UserProfileResponse)
async def get_user_profile_me(
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> UserProfileResponse:
    profile = await user_account_service.get_user_profile_data(current_user.uid)
    return UserProfileResponse(profile=profile)


@router.post("/users/me/profile", response_model=UserProfileUpdateResponse)
async def upsert_user_profile_me(
    payload: dict[str, object],
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> UserProfileUpdateResponse:
    auth_email = current_user.claims.get("email")

    try:
        profile = await user_account_service.upsert_user_profile_data(
            current_user.uid,
            payload,
            auth_email=auth_email if isinstance(auth_email, str) else None,
        )
    except UserProfileValidationError as exc:
        raise_bad_request(exc)

    return UserProfileUpdateResponse(profile=profile, updated=True)


@router.post("/users/me/onboarding", response_model=UserOnboardingResponse)
async def initialize_user_onboarding_me(
    request: UserOnboardingRequest,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> UserOnboardingResponse:
    auth_email = current_user.claims.get("email")

    try:
        normalized_username, profile = await user_account_service.initialize_onboarding_profile(
            current_user.uid,
            username=request.username,
            language=request.language,
            auth_email=auth_email if isinstance(auth_email, str) else None,
        )
    except OnboardingValidationError as exc:
        raise_bad_request(exc)
    except OnboardingUsernameUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    return UserOnboardingResponse(
        username=normalized_username,
        profile=profile,
        updated=True,
    )


@router.post("/users/me/email-pending", response_model=EmailPendingResponse)
async def set_email_pending_me(
    request: EmailPendingRequest,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> EmailPendingResponse:
    try:
        normalized_email = await user_account_service.set_email_pending(
            current_user.uid,
            request.email,
        )
    except EmailValidationError as exc:
        raise_bad_request(exc)

    return EmailPendingResponse(emailPending=normalized_email, updated=True)


@router.post("/users/me/avatar-metadata", response_model=AvatarMetadataResponse)
async def set_avatar_metadata_me(
    request: AvatarMetadataRequest,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> AvatarMetadataResponse:
    try:
        normalized_avatar_url, synced_at = await user_account_service.set_avatar_metadata(
            current_user.uid,
            request.avatarUrl,
        )
    except AvatarMetadataValidationError as exc:
        raise_bad_request(exc)

    return AvatarMetadataResponse(
        avatarUrl=normalized_avatar_url,
        avatarlastSyncedAt=synced_at,
        updated=True,
    )


@router.post("/users/me/avatar", response_model=AvatarMetadataResponse)
async def upload_avatar_me(
    file: UploadFile = File(...),
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> AvatarMetadataResponse:
    normalized_avatar_url, synced_at = await user_account_service.upload_avatar(
        current_user.uid,
        file,
    )

    return AvatarMetadataResponse(
        avatarUrl=normalized_avatar_url,
        avatarlastSyncedAt=synced_at,
        updated=True,
    )


@router.post("/users/me/delete", response_model=DeleteAccountResponse)
async def delete_account_me(
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> DeleteAccountResponse:
    await user_account_service.delete_account_data(current_user.uid)
    return DeleteAccountResponse(deleted=True)


@router.get("/users/me/export", response_model=UserExportResponse)
async def get_user_export_me(
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> UserExportResponse:
    profile, meals, my_meals, chat_messages, notifications, notification_prefs, feedback = (
        await user_account_service.get_user_export_data(current_user.uid)
    )

    return UserExportResponse(
        profile=profile,
        meals=meals,
        myMeals=my_meals,
        chatMessages=chat_messages,
        notifications=notifications,
        notificationPrefs=notification_prefs,
        feedback=feedback,
    )
