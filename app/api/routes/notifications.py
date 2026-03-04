from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import AuthenticatedUser, get_required_authenticated_user
from app.core.exceptions import FirestoreServiceError
from app.schemas.notification import (
    NotificationDeleteResponse,
    NotificationListResponse,
    NotificationPrefsResponse,
    NotificationPrefsUpdateRequest,
    NotificationPrefsUpdateResponse,
    NotificationUpsertResponse,
    UserNotificationItem,
)
from app.schemas.notification_plan import (
    NotificationPlanItem,
    NotificationPlanRequest,
    NotificationPlanResponse,
    NotificationTime,
)
from app.services import notification_plan_service, notification_service
from app.services.notification_service import (
    NotificationPrefsValidationError,
    NotificationValidationError,
)

router = APIRouter()


async def _list_notifications_for_user(*, user_id: str) -> NotificationListResponse:
    try:
        items = await notification_service.list_notifications(user_id)
    except FirestoreServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error",
        ) from exc

    return NotificationListResponse(items=items)


async def _upsert_notification_for_user(
    *,
    user_id: str,
    payload: UserNotificationItem,
) -> NotificationUpsertResponse:
    try:
        item = await notification_service.upsert_notification(
            user_id,
            payload.model_dump(),
        )
    except NotificationValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except FirestoreServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error",
        ) from exc

    return NotificationUpsertResponse(item=item, updated=True)


async def _delete_notification_for_user(
    *,
    user_id: str,
    notification_id: str,
) -> NotificationDeleteResponse:
    try:
        await notification_service.delete_notification(user_id, notification_id)
    except NotificationValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except FirestoreServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error",
        ) from exc

    return NotificationDeleteResponse(
        notificationId=notification_id,
        deleted=True,
    )


async def _get_notification_prefs_for_user(*, user_id: str) -> NotificationPrefsResponse:
    try:
        notifications = await notification_service.get_notification_prefs(user_id)
    except FirestoreServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error",
        ) from exc

    return NotificationPrefsResponse(notifications=notifications)


async def _update_notification_prefs_for_user(
    *,
    user_id: str,
    request: NotificationPrefsUpdateRequest,
) -> NotificationPrefsUpdateResponse:
    try:
        notifications = await notification_service.update_notification_prefs(
            user_id,
            request.notifications.model_dump(exclude_unset=True),
        )
    except NotificationPrefsValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except FirestoreServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error",
        ) from exc

    return NotificationPrefsUpdateResponse(
        notifications=notifications,
        updated=True,
    )


async def _reconcile_notification_plan_for_user(
    *,
    user_id: str,
    request: NotificationPlanRequest,
) -> NotificationPlanResponse:
    try:
        ai_style, plans = await notification_plan_service.get_notification_plan(
            user_id,
            start_iso=request.startIso,
            end_iso=request.endIso,
        )
    except FirestoreServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error",
        ) from exc

    return NotificationPlanResponse(
        aiStyle=ai_style,
        plans=[
            NotificationPlanItem(
                id=plan.id,
                type=plan.type,
                enabled=plan.enabled,
                text=plan.text,
                time=NotificationTime(hour=plan.time.hour, minute=plan.time.minute),
                days=plan.days,
                mealKind=plan.meal_kind,
                shouldSchedule=plan.should_schedule,
                missingKcal=plan.missing_kcal,
            )
            for plan in plans
        ],
    )


@router.post(
    "/users/me/notifications/reconcile-plan",
    response_model=NotificationPlanResponse,
)
async def reconcile_notification_plan_me(
    request: NotificationPlanRequest,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> NotificationPlanResponse:
    return await _reconcile_notification_plan_for_user(
        user_id=current_user.uid,
        request=request,
    )


@router.get("/users/me/notifications", response_model=NotificationListResponse)
async def list_notifications_me(
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> NotificationListResponse:
    return await _list_notifications_for_user(user_id=current_user.uid)


@router.post("/users/me/notifications", response_model=NotificationUpsertResponse)
async def upsert_notification_me(
    request: UserNotificationItem,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> NotificationUpsertResponse:
    return await _upsert_notification_for_user(
        user_id=current_user.uid,
        payload=request,
    )


@router.post(
    "/users/me/notifications/{notificationId}/delete",
    response_model=NotificationDeleteResponse,
)
async def delete_notification_me(
    notificationId: str,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> NotificationDeleteResponse:
    return await _delete_notification_for_user(
        user_id=current_user.uid,
        notification_id=notificationId,
    )


@router.get(
    "/users/me/notifications/preferences",
    response_model=NotificationPrefsResponse,
)
async def get_notification_prefs_me(
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> NotificationPrefsResponse:
    return await _get_notification_prefs_for_user(user_id=current_user.uid)


@router.post(
    "/users/me/notifications/preferences",
    response_model=NotificationPrefsUpdateResponse,
)
async def update_notification_prefs_me(
    request: NotificationPrefsUpdateRequest,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> NotificationPrefsUpdateResponse:
    return await _update_notification_prefs_for_user(
        user_id=current_user.uid,
        request=request,
    )
