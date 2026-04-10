import pytest

from app.services.reminder_engine.profile import classify_profile, policy_for_profile
from app.services.reminder_engine.types import (
    ReminderPersonalizationProfile,
    ReminderTimingPolicy,
)
from tests.reminder_engine._helpers import load_state_fixture


def test_personalization_profile_classifies_self_sufficient_user() -> None:
    state = load_state_fixture()
    state.habits.behavior.validLoggingConsistency28 = 0.82
    state.habits.behavior.dayCoverage14.validLoggedDays = 10
    state.habits.behavior.avgValidMealsPerValidLoggedDay14 = 3.4
    state.habits.behavior.validLoggingDays7 = 5
    state.habits.behavior.timingPatterns14.observedDays = 9

    assert classify_profile(state) == ReminderPersonalizationProfile(segment="self_sufficient")


def test_personalization_profile_classifies_responsive_user() -> None:
    state = load_state_fixture()
    state.habits.behavior.validLoggingConsistency28 = 0.58
    state.habits.behavior.dayCoverage14.validLoggedDays = 6
    state.habits.behavior.avgValidMealsPerValidLoggedDay14 = 2.6
    state.habits.behavior.validLoggingDays7 = 4
    state.habits.behavior.timingPatterns14.observedDays = 8

    assert classify_profile(state) == ReminderPersonalizationProfile(segment="responsive")


def test_personalization_profile_classifies_low_engagement_user() -> None:
    state = load_state_fixture()
    state.habits.behavior.validLoggingDays7 = 2
    state.habits.behavior.validLoggingConsistency28 = 0.29
    state.habits.behavior.dayCoverage14.validLoggedDays = 3
    state.habits.behavior.avgValidMealsPerValidLoggedDay14 = 1.4
    state.habits.behavior.timingPatterns14.observedDays = 2

    assert classify_profile(state) == ReminderPersonalizationProfile(segment="low_engagement")


def test_personalization_profile_falls_back_to_neutral() -> None:
    state = load_state_fixture()
    state.habits.behavior.validLoggingConsistency28 = 0.4
    state.habits.behavior.dayCoverage14.validLoggedDays = 4
    state.habits.behavior.avgValidMealsPerValidLoggedDay14 = 2.2
    state.habits.behavior.validLoggingDays7 = 3
    state.habits.behavior.timingPatterns14.observedDays = 5

    assert classify_profile(state) == ReminderPersonalizationProfile(segment="neutral")


@pytest.mark.parametrize(
    ("profile", "expected_policy"),
    [
        (
            ReminderPersonalizationProfile(segment="responsive"),
            ReminderTimingPolicy(
                strong_habit_observed_days_min=6,
                anchor_proximity_min=20,
                prefer_anchor_inside_window=True,
                complete_day_guarded=False,
            ),
        ),
        (
            ReminderPersonalizationProfile(segment="neutral"),
            ReminderTimingPolicy(
                strong_habit_observed_days_min=7,
                anchor_proximity_min=30,
                prefer_anchor_inside_window=True,
                complete_day_guarded=False,
            ),
        ),
        (
            ReminderPersonalizationProfile(segment="low_engagement"),
            ReminderTimingPolicy(
                strong_habit_observed_days_min=8,
                anchor_proximity_min=45,
                prefer_anchor_inside_window=False,
                complete_day_guarded=True,
            ),
        ),
        (
            ReminderPersonalizationProfile(segment="self_sufficient"),
            ReminderTimingPolicy(
                strong_habit_observed_days_min=8,
                anchor_proximity_min=35,
                prefer_anchor_inside_window=False,
                complete_day_guarded=True,
            ),
        ),
    ],
)
def test_personalization_timing_policy_is_bounded_and_deterministic(
    profile: ReminderPersonalizationProfile,
    expected_policy: ReminderTimingPolicy,
) -> None:
    policy = policy_for_profile(profile)

    assert policy == expected_policy
    assert 6 <= policy.strong_habit_observed_days_min <= 8
    assert 20 <= policy.anchor_proximity_min <= 45
