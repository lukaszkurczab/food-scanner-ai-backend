from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.core.datetime_utils import utc_now
from app.schemas.weekly_reports import WeeklyReportPeriod, WeeklyReportResponse
from app.services.weekly_report_aggregation import WeeklyAggregate, collect_weekly_aggregate
from app.services.weekly_report_selection import build_weekly_report_content
from app.services.weekly_report_signals import WeeklySignals, derive_weekly_signals

UTC = timezone.utc


@dataclass(frozen=True)
class WeeklyReportRequestContext:
    user_id: str
    period: WeeklyReportPeriod


@dataclass(frozen=True)
class WeeklyReportFoundation:
    aggregate: WeeklyAggregate
    signals: WeeklySignals


def _parse_day_key_or_raise(value: str, *, field_name: str) -> str:
    normalized = value.strip()
    try:
        return datetime.strptime(normalized, "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise ValueError(f"Invalid {field_name}. Expected YYYY-MM-DD.") from exc


def resolve_requested_week_end(
    week_end: str | None,
    *,
    now: datetime | None = None,
) -> str:
    computed_at = (now or utc_now()).astimezone(UTC)
    today = computed_at.date()

    if week_end is None:
        resolved = today - timedelta(days=1)
    else:
        resolved = datetime.strptime(
            _parse_day_key_or_raise(week_end, field_name="weekEnd"),
            "%Y-%m-%d",
        ).date()

    if resolved >= today:
        raise ValueError("weekEnd must be a closed day before today.")

    return resolved.isoformat()


def build_weekly_report_period(week_end: str) -> WeeklyReportPeriod:
    end_day = datetime.strptime(week_end, "%Y-%m-%d").date()
    start_day = end_day - timedelta(days=6)
    return WeeklyReportPeriod(
        startDay=start_day.isoformat(),
        endDay=end_day.isoformat(),
    )


async def get_weekly_report(
    user_id: str,
    *,
    week_end: str | None = None,
    now: datetime | None = None,
) -> WeeklyReportResponse:
    resolved_week_end = resolve_requested_week_end(week_end, now=now)
    period = build_weekly_report_period(resolved_week_end)
    context = WeeklyReportRequestContext(user_id=user_id, period=period)

    foundation = await _collect_weekly_report_foundation(context)
    if not foundation.signals.has_sufficient_data:
        return _build_insufficient_data_response(context)

    return _build_ready_response(context, foundation)


async def _collect_weekly_report_foundation(
    context: WeeklyReportRequestContext,
) -> WeeklyReportFoundation:
    aggregate = collect_weekly_aggregate(context.user_id, period=context.period)
    signals = derive_weekly_signals(aggregate)
    return WeeklyReportFoundation(aggregate=aggregate, signals=signals)


def _build_insufficient_data_response(
    context: WeeklyReportRequestContext,
) -> WeeklyReportResponse:
    return WeeklyReportResponse(
        status="insufficient_data",
        period=context.period,
        summary="Log a few complete days to unlock a weekly report.",
        insights=[],
        priorities=[],
    )


def _build_ready_response(
    context: WeeklyReportRequestContext,
    foundation: WeeklyReportFoundation,
) -> WeeklyReportResponse:
    content = build_weekly_report_content(foundation.signals)
    return WeeklyReportResponse(
        status="ready",
        period=context.period,
        summary=content.summary,
        insights=content.insights,
        priorities=content.priorities,
    )
