from dataclasses import dataclass, field


def _empty_preferences() -> list[str]:
    return []


def _empty_allergies() -> list[str]:
    return []


@dataclass(slots=True)
class UserProfile:
    user_id: str
    goal: str | None = None
    activity_level: str | None = None
    calorie_target: int | None = None
    preferences: list[str] = field(default_factory=_empty_preferences)
    allergies: list[str] = field(default_factory=_empty_allergies)
    language: str = "pl"
    ai_health_data_consent_at: str | None = None
    survey_completed: bool = False
