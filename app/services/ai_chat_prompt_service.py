"""Backend-owned chat prompt shaping for /ai/ask."""

from typing import Any


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _as_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _coarsen_range(value: Any, bucket_size: int, unit: str | None = None) -> str | None:
    number = _as_number(value)
    if number is None or number <= 0:
        return None
    start = int(number // bucket_size) * bucket_size
    end = start + bucket_size - 1
    label = f"{start}-{end}"
    return f"{label} {unit}" if unit else label


def _history_to_lines(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []

    lines: list[str] = []
    for item in value[-2:]:
        if isinstance(item, str):
            normalized = item.strip()
            if normalized:
                lines.append(normalized)
            continue

        if isinstance(item, dict):
            text = _as_string(item.get("text") or item.get("content"))
            if text:
                role = _as_string(item.get("from") or item.get("role")) or "user"
                lines.append(f"{role}: {text}")

    return lines


def _summarize_meals(meals: Any) -> str:
    if not isinstance(meals, list):
        return "none"
    meal_items: list[object] = meals
    if not meal_items:
        return "none"

    normalized: list[tuple[str, str]] = []
    for item in meal_items[:5]:
        if not isinstance(item, dict):
            continue
        timestamp = _as_string(item.get("timestamp") or item.get("createdAt")) or ""
        date_key = timestamp[:10] if len(timestamp) >= 10 else "unknown"
        name = _as_string(item.get("name") or item.get("type")) or "meal"
        normalized.append((date_key, name))

    if not normalized:
        return "none"

    normalized.sort(key=lambda item: item[0], reverse=True)
    preview = ",".join(f"{date}:{name}" for date, name in normalized[:5])
    return f"{len(meal_items)}|{preview}"


def _tone_from_profile(profile: dict[str, Any]) -> str:
    style = _as_string(profile.get("aiStyle")) or "none"
    return {
        "concise": "C",
        "detailed": "D",
        "friendly": "F",
    }.get(style, "N")


def _focus_from_profile(profile: dict[str, Any]) -> str:
    focus = _as_string(profile.get("aiFocus")) or "none"
    return {
        "mealPlanning": "MP",
        "analyzingMistakes": "AM",
        "motivation": "M",
    }.get(focus, "DEF")


def _derive_flags_and_avoid(profile: dict[str, Any]) -> tuple[list[str], list[str]]:
    preferences = set(_as_string_list(profile.get("preferences")))
    diseases = set(_as_string_list(profile.get("chronicDiseases")))
    allergies = set(_as_string_list(profile.get("allergies")))
    flags: list[str] = []
    avoid: list[str] = []

    if "vegan" in preferences:
        flags.append("vegan")
        avoid.extend(
            [
                "mieso",
                "wolowina",
                "kurczak",
                "ryba",
                "tunczyk",
                "losos",
                "jaja",
                "mleko",
                "ser",
                "jogurt",
            ]
        )
    elif "vegetarian" in preferences:
        flags.append("vegetarian")
        avoid.extend(["mieso", "ryba", "tunczyk", "losos", "kurczak", "wolowina"])

    for key in ("pescatarian", "keto", "lowCarb", "highProtein", "lowFat"):
        if key in preferences:
            flags.append(key)

    if "glutenFree" in preferences or "gluten" in allergies:
        flags.append("glutenFree")
        avoid.extend(
            [
                "pszenica",
                "jeczmien",
                "zyto",
                "makaron pszenny",
                "pieczywo pszenne",
            ]
        )

    if "dairyFree" in preferences or "lactose" in allergies:
        flags.append("dairyFree")
        avoid.extend(["mleko", "ser", "jogurt", "kefir", "maslanka", "serwatka"])

    if "peanuts" in allergies:
        flags.append("noPeanuts")
        avoid.extend(["orzeszki ziemne", "maslo orzechowe"])

    if "diabetes" in diseases:
        flags.append("diabetes")
    if "hypertension" in diseases:
        flags.append("hypertension")

    return flags, avoid


def _compact_profile(profile: dict[str, Any], language: str) -> str:
    units_system = _as_string(profile.get("unitsSystem")) or "metric"
    compact = {
        "g": _as_string(profile.get("goal")),
        "act": _as_string(profile.get("activityLevel")),
        "s": _as_string(profile.get("sex")),
        "a": _coarsen_range(profile.get("age"), 10),
        "h": (
            _coarsen_range(profile.get("height"), 10, "cm")
            if units_system == "metric"
            else None
        ),
        "w": _coarsen_range(profile.get("weight"), 10, "kg"),
        "kcal": _as_number(profile.get("calorieTarget")),
        "lang": language,
        "unit": units_system,
    }
    pairs = [f"{key}={value}" for key, value in compact.items() if value is not None]
    return "; ".join(pairs) if pairs else "none"


def _resolve_flags(context: dict[str, Any], profile: dict[str, Any]) -> list[str]:
    legacy = _as_string_list(context.get("flags"))
    if legacy:
        return legacy
    return _derive_flags_and_avoid(profile)[0]


def _resolve_avoid(context: dict[str, Any], profile: dict[str, Any]) -> list[str]:
    legacy = _as_string_list(context.get("avoid"))
    if legacy:
        return legacy
    return _derive_flags_and_avoid(profile)[1]


def _resolve_tone(context: dict[str, Any], profile: dict[str, Any]) -> str:
    legacy = _as_string(context.get("tone"))
    if legacy:
        return legacy
    return _tone_from_profile(profile)


def _resolve_focus(context: dict[str, Any], profile: dict[str, Any]) -> str:
    legacy = _as_string(context.get("focus"))
    if legacy:
        return legacy
    return _focus_from_profile(profile)


def _resolve_meals_summary(context: dict[str, Any]) -> str:
    legacy = _as_string(context.get("mealsSummary"))
    if legacy:
        return legacy
    return _summarize_meals(context.get("meals"))


def _resolve_profile_summary(context: dict[str, Any], language: str) -> str:
    legacy = _as_string(context.get("profile"))
    if legacy:
        return legacy
    return _compact_profile(_as_dict(context.get("profile")), language)


def _off_topic_reply(language: str) -> str:
    if language.lower().startswith("pl"):
        return (
            "To pytanie jest poza zakresem tego czatu. "
            "Moge pomoc tylko w tematach zywienia, diety i posilkow."
        )
    return (
        "This question is out of scope for this chat. "
        "I can only help with food, nutrition, and diet topics."
    )


def build_chat_prompt(
    message: str,
    context: dict[str, Any] | None,
    *,
    language: str = "pl",
) -> str:
    normalized_context = _as_dict(context)
    profile = _as_dict(normalized_context.get("profile"))
    flags = _resolve_flags(normalized_context, profile)
    avoid = _resolve_avoid(normalized_context, profile)
    tone = _resolve_tone(normalized_context, profile)
    focus = _resolve_focus(normalized_context, profile)
    meals_summary = _resolve_meals_summary(normalized_context)
    profile_summary = _resolve_profile_summary(normalized_context, language)
    history_lines = _history_to_lines(normalized_context.get("history"))
    history_summary = " | ".join(history_lines) if history_lines else "none"
    off_topic_reply = _off_topic_reply(language)

    sections = [
        "You are a food and nutrition assistant.",
        f"Reply in {language}.",
        "Stay within food, nutrition, meals, calories, macros, and healthy eating guidance.",
        f"If the user asks about non-diet topics, do not answer that topic and reply exactly with: {off_topic_reply}",
        "Use meal history as context, not as a strict template. Do not blindly repeat previously eaten meals unless the user asks for that.",
        "When suggesting meals, prefer practical healthier options and include short rationale (protein, fiber, calories/macros) when useful.",
        "If key constraints are missing for a recommendation, ask one short clarifying question before proposing a specific plan.",
        "Keep the answer practical and safe. Do not provide medical diagnosis or treatment advice.",
        f"TONE={tone}",
        f"FOCUS={focus}",
        f"FLAGS={','.join(flags) if flags else 'none'}",
        f"AVOID={','.join(avoid) if avoid else 'none'}",
        f"PROFILE={profile_summary}",
        f"MEALS={meals_summary}",
        f"HISTORY={history_summary}",
        f"USER_MESSAGE={message}",
    ]
    return "\n".join(sections)
