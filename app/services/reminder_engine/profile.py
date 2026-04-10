from __future__ import annotations

from app.schemas.nutrition_state import NutritionStateResponse
from app.services.reminder_engine.types import (
    LOW_ENGAGEMENT_PROFILE_MAX_CONSISTENCY28_EXCLUSIVE,
    LOW_ENGAGEMENT_PROFILE_MAX_VALID_DAYS14,
    LOW_ENGAGEMENT_PROFILE_MAX_VALID_LOGGING_DAYS7,
    LOW_ENGAGEMENT_TIMING_POLICY,
    NEUTRAL_TIMING_POLICY,
    RESPONSIVE_PROFILE_MIN_CONSISTENCY28,
    RESPONSIVE_PROFILE_MIN_OBSERVED_DAYS,
    RESPONSIVE_PROFILE_MIN_VALID_LOGGING_DAYS7,
    RESPONSIVE_TIMING_POLICY,
    SELF_SUFFICIENT_PROFILE_MIN_AVG_MEALS14,
    SELF_SUFFICIENT_PROFILE_MIN_CONSISTENCY28,
    SELF_SUFFICIENT_PROFILE_MIN_VALID_DAYS14,
    SELF_SUFFICIENT_TIMING_POLICY,
    ReminderPersonalizationProfile,
    ReminderTimingPolicy,
)


def classify_profile(state: NutritionStateResponse) -> ReminderPersonalizationProfile:
    return _build_personalization_profile(state=state)


def policy_for_profile(profile: ReminderPersonalizationProfile) -> ReminderTimingPolicy:
    return _timing_policy_for_profile(profile)


def _build_personalization_profile(
    *,
    state: NutritionStateResponse,
) -> ReminderPersonalizationProfile:
    """Classify reminder style from existing behavior signals only."""
    if _is_self_sufficient_profile(state):
        return ReminderPersonalizationProfile(segment="self_sufficient")

    if _is_responsive_profile(state):
        return ReminderPersonalizationProfile(segment="responsive")

    if _is_low_engagement_profile(state):
        return ReminderPersonalizationProfile(segment="low_engagement")

    return ReminderPersonalizationProfile(segment="neutral")


def _timing_policy_for_profile(
    profile: ReminderPersonalizationProfile,
) -> ReminderTimingPolicy:
    if profile.segment == "responsive":
        return RESPONSIVE_TIMING_POLICY
    if profile.segment == "low_engagement":
        return LOW_ENGAGEMENT_TIMING_POLICY
    if profile.segment == "self_sufficient":
        return SELF_SUFFICIENT_TIMING_POLICY
    return NEUTRAL_TIMING_POLICY


def _timing_policy_label(profile: ReminderPersonalizationProfile) -> str:
    if profile.segment == "responsive":
        return "responsive"
    if profile.segment == "low_engagement":
        return "low_engagement_direct"
    if profile.segment == "self_sufficient":
        return "self_sufficient_direct"
    return "neutral"


def _is_self_sufficient_profile(state: NutritionStateResponse) -> bool:
    behavior = state.habits.behavior
    return (
        behavior.validLoggingConsistency28 >= SELF_SUFFICIENT_PROFILE_MIN_CONSISTENCY28
        and behavior.dayCoverage14.validLoggedDays >= SELF_SUFFICIENT_PROFILE_MIN_VALID_DAYS14
        and behavior.avgValidMealsPerValidLoggedDay14 >= SELF_SUFFICIENT_PROFILE_MIN_AVG_MEALS14
    )


def _is_responsive_profile(state: NutritionStateResponse) -> bool:
    behavior = state.habits.behavior
    timing = behavior.timingPatterns14
    return (
        timing.observedDays >= RESPONSIVE_PROFILE_MIN_OBSERVED_DAYS
        and behavior.validLoggingDays7 >= RESPONSIVE_PROFILE_MIN_VALID_LOGGING_DAYS7
        and behavior.validLoggingConsistency28 >= RESPONSIVE_PROFILE_MIN_CONSISTENCY28
    )


def _is_low_engagement_profile(state: NutritionStateResponse) -> bool:
    behavior = state.habits.behavior
    return (
        behavior.validLoggingDays7 <= LOW_ENGAGEMENT_PROFILE_MAX_VALID_LOGGING_DAYS7
        or behavior.validLoggingConsistency28 < LOW_ENGAGEMENT_PROFILE_MAX_CONSISTENCY28_EXCLUSIVE
        or behavior.dayCoverage14.validLoggedDays <= LOW_ENGAGEMENT_PROFILE_MAX_VALID_DAYS14
    )
