from fastapi import APIRouter, Depends, Query

from app.api.deps import AuthenticatedUser, get_required_authenticated_user
from app.api.http_errors import raise_bad_request, raise_http_exception, raise_service_unavailable
from app.core.exceptions import (
    FirestoreServiceError,
    ReminderDecisionContractError,
    ReminderUnavailableError,
    SmartRemindersDisabledError,
    StateDisabledError,
)
from app.schemas.reminders import ReminderDecision
from app.services.reminder_service import get_reminder_decision

router = APIRouter()


@router.get(
    "/users/me/reminders/decision",
    response_model=ReminderDecision,
    summary="Get Smart Reminders v1 decision for a user day",
    description=(
        "Returns the v2 Smart Reminders decision surface for the authenticated "
        "user and day. This is a backend decision API only. It does not send, "
        "schedule, or deliver notifications. Smart Reminders v1 do not consume "
        "Coach Insights as an input."
    ),
)
async def get_user_reminder_decision_me(
    day: str | None = Query(default=None, min_length=10, max_length=10),
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> ReminderDecision:
    try:
        return await get_reminder_decision(current_user.uid, day_key=day)
    except (
        StateDisabledError,
        SmartRemindersDisabledError,
        ReminderUnavailableError,
    ) as exc:
        raise_service_unavailable(exc, detail="Smart reminders are unavailable")
    except ReminderDecisionContractError as exc:
        raise_http_exception(
            status_code=500,
            detail="Reminder decision contract violation",
            cause=exc,
        )
    except FirestoreServiceError as exc:
        raise_http_exception(
            status_code=500,
            detail="Failed to compute reminder decision",
            cause=exc,
        )
    except ValueError as exc:
        raise_bad_request(exc)
