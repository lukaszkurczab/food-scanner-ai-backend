from fastapi import APIRouter, Depends, Query

from app.api.deps import AuthenticatedUser, get_required_authenticated_user
from app.api.http_errors import raise_bad_request, raise_http_exception, raise_service_unavailable
from app.core.exceptions import CoachUnavailableError, FirestoreServiceError, StateDisabledError
from app.schemas.coach import CoachResponse
from app.services.coach_service import get_coach_response

router = APIRouter()


@router.get(
    "/users/me/coach",
    response_model=CoachResponse,
    summary="Get proactive coach insights for a user day",
    description=(
        "Returns the v2 Coach Insights surface for the authenticated user and day. "
        "The response is derived from nutrition state and habit signals, without AI "
        "in the critical path."
    ),
)
async def get_user_coach_me(
    day: str | None = Query(default=None, min_length=10, max_length=10),
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> CoachResponse:
    try:
        return await get_coach_response(current_user.uid, day_key=day)
    except ValueError as exc:
        raise_bad_request(exc)
    except (StateDisabledError, CoachUnavailableError) as exc:
        raise_service_unavailable(exc, detail="Coach insights are unavailable")
    except FirestoreServiceError as exc:
        raise_http_exception(
            status_code=500,
            detail="Failed to compute coach insights",
            cause=exc,
        )
