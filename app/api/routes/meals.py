from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status

from app.api.deps import AuthenticatedUser, get_required_authenticated_user
from app.core.exceptions import FirestoreServiceError
from app.schemas.meal import (
    MealChangesPageResponse,
    MealDeleteRequest,
    MealDeleteResponse,
    MealPhotoUploadResponse,
    MealsHistoryPageResponse,
    MealUpsertRequest,
    MealUpsertResponse,
)
from app.services import meal_service

router = APIRouter()


def _to_range(min_value: float | None, max_value: float | None) -> tuple[float, float] | None:
    if min_value is None and max_value is None:
        return None
    if min_value is None or max_value is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Both range values are required",
        )
    if min_value > max_value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid range",
        )
    return min_value, max_value


@router.get("/users/me/meals/history", response_model=MealsHistoryPageResponse)
async def get_meals_history_me(
    limit: int = Query(default=20, ge=1, le=100),
    beforeCursor: str | None = Query(default=None),
    caloriesMin: float | None = Query(default=None, ge=0),
    caloriesMax: float | None = Query(default=None, ge=0),
    proteinMin: float | None = Query(default=None, ge=0),
    proteinMax: float | None = Query(default=None, ge=0),
    carbsMin: float | None = Query(default=None, ge=0),
    carbsMax: float | None = Query(default=None, ge=0),
    fatMin: float | None = Query(default=None, ge=0),
    fatMax: float | None = Query(default=None, ge=0),
    timestampStart: str | None = Query(default=None),
    timestampEnd: str | None = Query(default=None),
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> MealsHistoryPageResponse:
    try:
        items, next_cursor = await meal_service.list_history(
            current_user.uid,
            limit_count=limit,
            before_cursor=beforeCursor,
            calories=_to_range(caloriesMin, caloriesMax),
            protein=_to_range(proteinMin, proteinMax),
            carbs=_to_range(carbsMin, carbsMax),
            fat=_to_range(fatMin, fatMax),
            timestamp_start=timestampStart,
            timestamp_end=timestampEnd,
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

    return MealsHistoryPageResponse(items=items, nextCursor=next_cursor)


@router.get("/users/me/meals/photo-url", response_model=MealPhotoUploadResponse)
async def get_meal_photo_url_me(
    mealId: str | None = Query(default=None),
    imageId: str | None = Query(default=None),
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> MealPhotoUploadResponse:
    try:
        payload = await meal_service.resolve_photo(
            current_user.uid,
            meal_id=mealId,
            image_id=imageId,
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

    return MealPhotoUploadResponse(**payload)


@router.get("/users/me/meals/changes", response_model=MealChangesPageResponse)
async def get_meal_changes_me(
    limit: int = Query(default=100, ge=1, le=250),
    afterCursor: str | None = Query(default=None),
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> MealChangesPageResponse:
    try:
        items, next_cursor = await meal_service.list_changes(
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


@router.post("/users/me/meals/photo", response_model=MealPhotoUploadResponse)
async def upload_meal_photo_me(
    file: UploadFile = File(...),
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> MealPhotoUploadResponse:
    try:
        payload = await meal_service.upload_photo(current_user.uid, file)
    except FirestoreServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error",
        ) from exc

    return MealPhotoUploadResponse(**payload)


@router.post("/users/me/meals", response_model=MealUpsertResponse)
async def upsert_meal_me(
    request: MealUpsertRequest,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> MealUpsertResponse:
    try:
        meal = await meal_service.upsert_meal(current_user.uid, request.model_dump())
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


@router.post("/users/me/meals/{mealId}/delete", response_model=MealDeleteResponse)
async def delete_meal_me(
    mealId: str,
    request: MealDeleteRequest,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> MealDeleteResponse:
    try:
        meal = await meal_service.mark_deleted(
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
