"""Helpers for masking sensitive user input before sending prompts to AI."""

from __future__ import annotations

import re
from typing import Any, cast


CONTROL_KEYS = {"language", "lang", "actionType", "action_type"}
PROFILE_RANGE_KEYS = {"age", "height", "heightInch", "weight"}
PROFILE_TEXT_KEYS = {
    "chronicDiseasesOther",
    "allergiesOther",
    "lifestyle",
    "aiFocusOther",
    "aiNote",
}


def _sanitize_free_text(value: str) -> str:
    def replace_number(match: re.Match[str]) -> str:
        try:
            num = int(match.group(0))
        except ValueError:
            return match.group(0)
        if 10 <= num <= 120:
            lower = (num // 10) * 10
            upper = lower + 10
            return f"{lower}-{upper}"
        return match.group(0)

    sanitized = re.sub(r"\b\d+\b", replace_number, value)
    sanitized = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "[email]", sanitized)
    return sanitized


def _coarsen_profile_number(value: Any) -> str | None:
    number: int | None = None
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        number = int(value)
    elif isinstance(value, str):
        digits = re.sub(r"[^0-9]", "", value)
        if digits:
            number = int(digits)

    if number is None or number <= 0:
        return None

    lower = (number // 10) * 10
    upper = lower + 10
    return f"{lower}-{upper}"


def sanitize_request(message: str, context: dict[str, object] | None = None) -> str:
    """Sanitize free-form user input before sending prompts to the AI provider."""
    del context
    return _sanitize_free_text(message)


def _sanitize_history(value: Any) -> list[Any]:
    if not isinstance(value, list):
        return []

    sanitized: list[Any] = []
    for item in value:
        if isinstance(item, str):
            sanitized.append(_sanitize_free_text(item))
            continue

        if isinstance(item, dict):
            source = cast(dict[str, Any], item)
            next_item: dict[str, Any] = dict(source)
            for key in ("text", "content"):
                raw = next_item.get(key)
                if isinstance(raw, str):
                    next_item[key] = _sanitize_free_text(raw)
            sanitized.append(next_item)

    return sanitized


def _sanitize_meals(value: Any) -> list[Any]:
    if not isinstance(value, list):
        return []

    sanitized: list[Any] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        source = cast(dict[str, Any], item)
        next_item: dict[str, Any] = dict(source)
        for key in ("name", "notes"):
            raw = next_item.get(key)
            if isinstance(raw, str):
                next_item[key] = _sanitize_free_text(raw)
        sanitized.append(next_item)

    return sanitized


def _sanitize_profile(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}

    source = cast(dict[str, Any], value)
    sanitized: dict[str, Any] = dict(source)

    for key in PROFILE_RANGE_KEYS:
        if key in sanitized:
            coerced = _coarsen_profile_number(sanitized.get(key))
            if coerced is not None:
                sanitized[key] = coerced

    for key in PROFILE_TEXT_KEYS:
        raw = sanitized.get(key)
        if isinstance(raw, str):
            sanitized[key] = _sanitize_free_text(raw)

    return sanitized


def sanitize_context(context: dict[str, object] | None) -> dict[str, object] | None:
    if context is None:
        return None

    sanitized: dict[str, object] = {}

    for key, value in context.items():
        if key in CONTROL_KEYS:
            sanitized[key] = value
        elif key == "profile":
            if isinstance(value, str):
                sanitized[key] = value
            else:
                sanitized[key] = _sanitize_profile(value)
        elif key == "history":
            sanitized[key] = _sanitize_history(value)
        elif key == "meals":
            sanitized[key] = _sanitize_meals(value)
        elif isinstance(value, str):
            sanitized[key] = _sanitize_free_text(value)
        else:
            sanitized[key] = value

    return sanitized
