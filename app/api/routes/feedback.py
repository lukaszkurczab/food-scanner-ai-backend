from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from app.api.deps import AuthenticatedUser, get_required_authenticated_user
from app.core.exceptions import FirestoreServiceError
from app.schemas.feedback import FeedbackCreateResponse
from app.services import feedback_service
from app.services.feedback_service import FeedbackValidationError

router = APIRouter()


@router.post("/users/me/feedback", response_model=FeedbackCreateResponse)
async def create_feedback_me(
    message: str = Form(...),
    deviceModelName: str | None = Form(default=None),
    deviceOsName: str | None = Form(default=None),
    deviceOsVersion: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> FeedbackCreateResponse:
    try:
        feedback = await feedback_service.create_feedback(
            user_id=current_user.uid,
            message=message,
            email=current_user.claims.get("email")
            if isinstance(current_user.claims.get("email"), str)
            else None,
            device_info={
                "modelName": deviceModelName,
                "osName": deviceOsName,
                "osVersion": deviceOsVersion,
            },
            attachment=file,
        )
    except FeedbackValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except FirestoreServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error",
        ) from exc

    return FeedbackCreateResponse(feedback=feedback, created=True)
