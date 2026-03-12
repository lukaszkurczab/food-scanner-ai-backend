"""Shared datetime helpers for AI credits and billing integrations."""

from calendar import monthrange
from datetime import datetime, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def add_one_month_clamped(anchor_at: datetime) -> datetime:
    normalized_anchor = ensure_utc_datetime(anchor_at)
    next_year = normalized_anchor.year
    next_month = normalized_anchor.month + 1
    if next_month > 12:
        next_month = 1
        next_year += 1

    max_day = monthrange(next_year, next_month)[1]
    next_day = min(normalized_anchor.day, max_day)

    return normalized_anchor.replace(year=next_year, month=next_month, day=next_day)


def parse_flexible_datetime(value: object) -> datetime | None:
    """Parse datetime from various formats: datetime, timestamp (ms/s), ISO string."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, int | float):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return None
        if normalized.isdigit():
            return parse_flexible_datetime(int(normalized))
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return None
