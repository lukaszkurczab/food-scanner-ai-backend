from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status

from app.api.deps import AuthenticatedUser, get_required_authenticated_user
from app.core.exceptions import FirestoreServiceError
from app.schemas.meal import (
    MealChangesPageResponse,
    MealDeleteRequest,
    MealDeleteResponse,
    MealPhotoUploadResponse,
    MealUpsertRequest,
    MealUpsertResponse,
)
from app.services import my_meal_service

router = APIRouter()


@router.get("/users/me/my-meals/changes", response_model=MealChangesPageResponse)
async def get_my_meal_changes_me(
    limit: int = Query(default=100, ge=1, le=250),
    afterCursor: str | None = Query(default=None),
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> MealChangesPageResponse:
    try:
        items, next_cursor = await my_meal_service.list_changes(
            current_user.uid,
            limit_count=limit,
            after_cursor=afterCursor,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except FirestoreServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error",
        ) from exc

    return MealChangesPageResponse(items=items, nextCursor=next_cursor)


@router.post("/users/me/my-meals", response_model=MealUpsertResponse)
async def upsert_my_meal_me(
    request: MealUpsertRequest,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> MealUpsertResponse:
    try:
        meal = await my_meal_service.upsert_saved_meal(
            current_user.uid,
            request.model_dump(),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except FirestoreServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error",
        ) from exc

    return MealUpsertResponse(meal=meal, updated=True)


@router.post("/users/me/my-meals/{mealId}/delete", response_model=MealDeleteResponse)
async def delete_my_meal_me(
    mealId: str,
    request: MealDeleteRequest,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> MealDeleteResponse:
    try:
        meal = await my_meal_service.mark_deleted(
            current_user.uid,
            mealId,
            updated_at=request.updatedAt,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except FirestoreServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error",
        ) from exc

    return MealDeleteResponse(
        mealId=meal["cloudId"],
        updatedAt=meal["updatedAt"],
        deleted=True,
    )


@router.post(
    "/users/me/my-meals/{mealId}/photo",
    response_model=MealPhotoUploadResponse,
)
async def upload_my_meal_photo_me(
    mealId: str,
    file: UploadFile = File(...),
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> MealPhotoUploadResponse:
    try:
        payload = await my_meal_service.upload_photo(
            current_user.uid,
            mealId,
            file,
        )
    except FirestoreServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error",
        ) from exc

    return MealPhotoUploadResponse(**payload)
