from fastapi import APIRouter, Depends, Query

from app.api.deps import AuthenticatedUser, get_required_authenticated_user
from app.api.http_errors import raise_bad_request, raise_http_exception
from app.core.exceptions import FirestoreServiceError
from app.services import ai_credits_service
from app.schemas.weekly_reports import WeeklyReportResponse
from app.services.weekly_report_service import get_weekly_report

router = APIRouter()


@router.get(
    "/users/me/reports/weekly",
    response_model=WeeklyReportResponse,
    summary="Get weekly synthesis report for a closed user week",
    description=(
        "Returns the v2 Weekly Reports surface for the authenticated user and a "
        "closed 7-day window. The payload is bounded, deterministic, and "
        "reserved for backend-first weekly synthesis."
    ),
)
async def get_user_weekly_report_me(
    weekEnd: str | None = Query(default=None, min_length=10, max_length=10),
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> WeeklyReportResponse:
    try:
        credits_status = await ai_credits_service.get_credits_status(current_user.uid)
        if credits_status.tier != "premium":
            raise_http_exception(
                status_code=403,
                detail="WEEKLY_REPORT_PREMIUM_REQUIRED",
            )

        return await get_weekly_report(current_user.uid, week_end=weekEnd)
    except ValueError as exc:
        raise_bad_request(exc)
    except FirestoreServiceError as exc:
        raise_http_exception(
            status_code=500,
            detail="Failed to compute weekly report",
            cause=exc,
        )
