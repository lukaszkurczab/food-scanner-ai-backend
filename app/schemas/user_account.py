from typing import Any
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class EmailPendingRequest(BaseModel):
    email: str = Field(min_length=3)


class EmailPendingResponse(BaseModel):
    emailPending: str
    updated: bool


class DeleteAccountResponse(BaseModel):
    deleted: bool


class AvatarMetadataRequest(BaseModel):
    avatarUrl: str = Field(min_length=1)


class AvatarMetadataResponse(BaseModel):
    avatarUrl: str
    avatarlastSyncedAt: str
    updated: bool


def _dict_items_default() -> list[dict[str, Any]]:
    return []


class UserExportResponse(BaseModel):
    profile: dict[str, Any] | None
    meals: list[dict[str, Any]]
    myMeals: list[dict[str, Any]] = Field(default_factory=_dict_items_default)
    chatMessages: list[dict[str, Any]]
    notifications: list[dict[str, Any]] = Field(default_factory=_dict_items_default)
    notificationPrefs: dict[str, Any] = Field(default_factory=dict)
    feedback: list[dict[str, Any]] = Field(default_factory=_dict_items_default)


class UserProfileResponse(BaseModel):
    profile: dict[str, Any] | None


class UserProfileUpdateResponse(UserProfileResponse):
    updated: bool


PreferenceValue = Literal[
    "lowCarb",
    "keto",
    "highProtein",
    "highCarb",
    "lowFat",
    "balanced",
    "vegetarian",
    "vegan",
    "pescatarian",
    "mediterranean",
    "glutenFree",
    "dairyFree",
    "paleo",
]

ChronicDiseaseValue = Literal["none", "diabetes", "hypertension", "asthma", "other"]
AllergyValue = Literal["none", "peanuts", "gluten", "lactose", "other"]
AiStyleValue = Literal["none", "concise", "friendly", "detailed"]
AiFocusValue = Literal["none", "mealPlanning", "analyzingMistakes", "motivation"]
UnitsSystemValue = Literal["metric", "imperial"]
ActivityLevelValue = Literal["sedentary", "light", "moderate", "active", "very_active", ""]
GoalValue = Literal["lose", "maintain", "increase", ""]
SexValue = Literal["male", "female"]
LanguageValue = Literal["en", "pl"]


class UserProfilePatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unitsSystem: UnitsSystemValue | None = Field(default=None)
    age: str | None = Field(default=None, max_length=4)
    sex: SexValue | None = Field(default=None)
    height: str | None = Field(default=None, max_length=8)
    heightInch: str | None = Field(default=None, max_length=8)
    weight: str | None = Field(default=None, max_length=8)
    preferences: list[PreferenceValue] | None = Field(default=None)
    activityLevel: ActivityLevelValue | None = Field(default=None)
    goal: GoalValue | None = Field(default=None)
    calorieDeficit: int | None = Field(default=None, ge=0, le=3000)
    calorieSurplus: int | None = Field(default=None, ge=0, le=3000)
    chronicDiseases: list[ChronicDiseaseValue] | None = Field(default=None)
    chronicDiseasesOther: str | None = Field(default=None, max_length=120)
    allergies: list[AllergyValue] | None = Field(default=None)
    allergiesOther: str | None = Field(default=None, max_length=120)
    lifestyle: str | None = Field(default=None, max_length=160)
    aiStyle: AiStyleValue | None = Field(default=None)
    aiFocus: AiFocusValue | None = Field(default=None)
    aiFocusOther: str | None = Field(default=None, max_length=120)
    aiNote: str | None = Field(default=None, max_length=600)
    surveyComplited: bool | None = Field(default=None)
    surveyCompletedAt: str | None = Field(default=None, max_length=64)
    calorieTarget: int | None = Field(default=None, ge=0, le=10000)
    darkTheme: bool | None = Field(default=None)
    language: LanguageValue | None = Field(default=None)

    @field_validator("preferences", "chronicDiseases", "allergies")
    @classmethod
    def _normalize_string_lists(
        cls,
        value: list[str] | None,
    ) -> list[str] | None:
        if value is None:
            return value
        # Keep payload deterministic and bound write size.
        deduped = list(dict.fromkeys(value))
        if len(deduped) > 16:
            raise ValueError("Too many items in profile list field.")
        return deduped

    @model_validator(mode="after")
    def _ensure_non_empty_patch(self) -> "UserProfilePatchRequest":
        if not self.model_fields_set:
            raise ValueError("Profile patch payload must not be empty.")
        return self

    def to_patch(self) -> dict[str, Any]:
        return self.model_dump(exclude_unset=True)


class UserOnboardingRequest(BaseModel):
    username: str = Field(min_length=1)
    language: str | None = Field(default=None)


class UserOnboardingResponse(BaseModel):
    username: str
    profile: dict[str, Any]
    updated: bool
