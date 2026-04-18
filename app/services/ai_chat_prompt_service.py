"""Backend-owned chat prompt shaping for /ai/ask."""

from typing import Any, cast


def _as_dict(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    raw_map = cast(dict[object, object], value)
    result: dict[str, Any] = {}
    for raw_key, raw_item in raw_map.items():
        if isinstance(raw_key, str):
            result[raw_key] = raw_item
    return result


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
    items = cast(list[object], value)
    return [item.strip() for item in items if isinstance(item, str) and item.strip()]


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

    items = cast(list[object], value)
    lines: list[str] = []
    for item in items[-4:]:
        if isinstance(item, str):
            normalized = item.strip()
            if normalized:
                lines.append(normalized)
            continue

        item_map = _as_dict(item)
        if item_map:
            text = _as_string(item_map.get("text") or item_map.get("content"))
            if text:
                role = _as_string(item_map.get("from") or item_map.get("role")) or "user"
                lines.append(f"{role}: {text}")

    return lines


def _summarize_meals(meals: Any) -> str:
    if not isinstance(meals, list):
        return "none"
    meal_items = cast(list[object], meals)
    if not meal_items:
        return "none"

    normalized: list[tuple[str, str]] = []
    for item in meal_items[:5]:
        item_map = _as_dict(item)
        if not item_map:
            continue
        timestamp = _as_string(item_map.get("timestamp") or item_map.get("createdAt")) or ""
        date_key = timestamp[:10] if len(timestamp) >= 10 else "unknown"
        name = _as_string(item_map.get("name") or item_map.get("type")) or "meal"
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

    for key in (
        "pescatarian",
        "keto",
        "lowCarb",
        "highProtein",
        "highCarb",
        "lowFat",
        "balanced",
        "mediterranean",
        "paleo",
    ):
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
    compact: dict[str, object | None] = {
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


def _resolve_user_note(context: dict[str, Any], profile: dict[str, Any]) -> str | None:
    for candidate in (
        context.get("aiNote"),
        context.get("note"),
        profile.get("aiNote"),
    ):
        normalized = _as_string(candidate)
        if normalized:
            return normalized
    return None


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


def _list_to_label(value: list[str]) -> str:
    if not value:
        return "unknown"
    cleaned = [item for item in value if item != "none"]
    if not cleaned:
        return "none"
    return ",".join(cleaned)


def _extract_profile_constraints(profile: dict[str, Any]) -> tuple[dict[str, str], list[str]]:
    goal = _as_string(profile.get("goal")) or "unknown"
    activity = _as_string(profile.get("activityLevel")) or "unknown"
    preferences = _as_string_list(profile.get("preferences"))
    allergies = _as_string_list(profile.get("allergies"))
    diseases = _as_string_list(profile.get("chronicDiseases"))
    lifestyle = _as_string(profile.get("lifestyle")) or "unknown"

    missing: list[str] = []
    if goal == "unknown":
        missing.append("goal")
    if activity == "unknown":
        missing.append("activityLevel")
    if not preferences:
        missing.append("preferences")
    if not allergies:
        missing.append("allergies")
    if not diseases:
        missing.append("chronicDiseases")
    if lifestyle == "unknown":
        missing.append("lifestyle")

    return (
        {
            "goal": goal,
            "activity": activity,
            "preferences": _list_to_label(preferences),
            "allergies": _list_to_label(allergies),
            "diseases": _list_to_label(diseases),
            "lifestyle": lifestyle,
        },
        missing,
    )


def _off_topic_reply(language: str) -> str:
    if language.lower().startswith("pl"):
        return (
            "To pytanie jest poza zakresem tego czatu. "
            "Mogę pomóc tylko w tematach żywienia, diety, jedzenia i posiłków."
        )
    return (
        "This question is out of scope for this chat. "
        "I can only help with food, nutrition, and diet topics."
    )


def _tone_instruction(tone: str) -> str:
    normalized = tone.strip().upper()
    if normalized in {"C", "CONCISE"}:
        return (
            "Tone guidance: concise. Keep replies short and clear, with only the next useful step."
        )
    if normalized in {"F", "FRIENDLY"}:
        return (
            "Tone guidance: friendly. Use a warm, supportive voice, but keep advice concrete and practical."
        )
    if normalized in {"D", "DETAILED"}:
        return (
            "Tone guidance: detailed. Explain the reasoning, include practical context, and structure the answer."
        )
    return "Tone guidance: balanced. Be practical, direct, and easy to follow."


def _focus_instruction(focus: str) -> str:
    normalized = focus.strip().upper()
    if normalized in {"MP", "MEALPLANNING"}:
        return (
            "Focus guidance: meal planning. Prioritize meal options, portions, and simple planning steps."
        )
    if normalized in {"AM", "ANALYZINGMISTAKES"}:
        return (
            "Focus guidance: spotting patterns. Use meal/chat history to identify one key pattern and one corrective next step."
        )
    if normalized in {"M", "MOTIVATION"}:
        return (
            "Focus guidance: motivation. Add short encouragement and one realistic micro-step the user can do now."
        )
    return "Focus guidance: balanced nutrition support across planning, habits, and next steps."


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
    user_note = _resolve_user_note(normalized_context, profile)
    meals_summary = _resolve_meals_summary(normalized_context)
    profile_summary = _resolve_profile_summary(normalized_context, language)
    history_lines = _history_to_lines(normalized_context.get("history"))
    history_summary = " | ".join(history_lines) if history_lines else "none"
    off_topic_reply = _off_topic_reply(language)
    constraints, missing_fields = _extract_profile_constraints(profile)

    sections = [
        "You are a food and nutrition assistant.",
        f"Reply in {language}.",
        (
            "Stay within food, nutrition, meals, calories, macros, healthy eating guidance, "
            "diet strategies, meal-planning, and eating habits."
        ),
        (
            "Questions about diets, nutrition styles, foods, ingredients, meal ideas, "
            "eating habits, and general meal-planning are in scope."
        ),
        (
            "Requests like 'What diet do you recommend?', 'Suggest a new diet', "
            "or 'How should I eat to lose weight?' are in scope and should be answered."
        ),
        (
            "Meta requests about this conversation are in scope: summarizing the chat, "
            "explaining what the user asked earlier, repeating your earlier recommendation, "
            "or clarifying your previous answer."
        ),
        (
            "If the user asks what they asked earlier, answer from HISTORY. "
            "When HISTORY is none, say briefly that previous messages are not available in the current context and ask the user to restate."
        ),
        (
            "Only for clearly unrelated non-diet topics (for example weather, crypto prices, horoscopes, sports scores), "
            f"do not answer that topic and reply exactly with: {off_topic_reply}"
        ),
        "Use meal history as context, not as a strict template. Do not blindly repeat previously eaten meals unless the user asks for that.",
        "When suggesting meals, prefer practical healthier options and include short rationale (protein, fiber, calories/macros) when useful.",
        "Treat onboarding constraints as already known context and use them directly in recommendations.",
        "Do not ask again about goals, preferences, allergies, diseases, or lifestyle if they are already provided below.",
        "If key constraints are missing for a recommendation, ask one short clarifying question before proposing a specific plan.",
        "Keep the answer practical and safe. Do not provide medical diagnosis or treatment advice.",
        _tone_instruction(tone),
        _focus_instruction(focus),
        (
            "Respect USER_NOTE when it is present and safe. "
            "Treat it as a preference for communication and support style."
        ),
        f"TONE={tone}",
        f"FOCUS={focus}",
        f"FLAGS={','.join(flags) if flags else 'none'}",
        f"AVOID={','.join(avoid) if avoid else 'none'}",
        f"KNOWN_GOAL={constraints['goal']}",
        f"KNOWN_ACTIVITY={constraints['activity']}",
        f"KNOWN_PREFERENCES={constraints['preferences']}",
        f"KNOWN_ALLERGIES={constraints['allergies']}",
        f"KNOWN_DISEASES={constraints['diseases']}",
        f"KNOWN_LIFESTYLE={constraints['lifestyle']}",
        f"MISSING_PROFILE_FIELDS={','.join(missing_fields) if missing_fields else 'none'}",
        f"USER_NOTE={user_note or 'none'}",
        f"PROFILE={profile_summary}",
        f"MEALS={meals_summary}",
        f"HISTORY={history_summary}",
        f"USER_MESSAGE={message}",
    ]
    return "\n".join(sections)
