from __future__ import annotations

import json

from app.domain.chat.prompt_composer import PromptComposer


def _base_grounding() -> dict:
    return {
        "planner": {
            "taskType": "mixed_capability_answer",
            "responseMode": "assessment_plus_guidance",
            "needsFollowUp": False,
            "capabilities": [
                "resolve_time_scope",
                "get_goal_context",
                "get_nutrition_period_summary",
            ],
        },
        "scope": {"type": "calendar_week"},
        "nutritionSummary": {"loggingCoverage": {"coverageLevel": "low"}},
    }


def test_prompt_composer_builds_structured_messages_without_blob_sections() -> None:
    composer = PromptComposer()
    prompt_input = composer.build_prompt_input(
        language="pl",
        response_mode="assessment_plus_guidance",
        grounding=_base_grounding(),
        user_message="Jak jadlem w tym tygodniu?",
    )

    messages = composer.compose_messages(prompt_input)
    assert len(messages) == 3
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "developer"
    assert messages[2]["role"] == "user"
    assert messages[2]["content"] == "Jak jadlem w tym tygodniu?"

    developer_payload = json.loads(messages[1]["content"])
    assert developer_payload["contract"] == "fitaly_chat_v2_grounded_response"
    assert "grounding" in developer_payload

    # Guard against legacy PROFILE/HISTORY prompt blobs.
    developer_raw = messages[1]["content"]
    assert "PROFILE=" not in developer_raw
    assert "HISTORY=" not in developer_raw
    assert "MEALS_CONTEXT=" not in developer_raw


def test_prompt_composer_enforces_verdict_first_blueprint_for_analytical_modes() -> None:
    composer = PromptComposer()
    prompt_input = composer.build_prompt_input(
        language="pl",
        response_mode="assessment_plus_guidance",
        grounding=_base_grounding(),
        user_message="Ocen moj tydzien i cel redukcji.",
    )

    messages = composer.compose_messages(prompt_input)
    developer_payload = json.loads(messages[1]["content"])

    assert developer_payload["responseShape"] == "mixed_weekly_summary_and_goal"
    blueprint = developer_payload["responseBlueprint"]
    assert blueprint["style"] == "verdict_first_product_analysis"
    assert blueprint["order"] == [
        "verdict",
        "coverage_data_quality",
        "key_observations",
        "practical_next_step",
        "optional_focused_follow_up",
    ]


def test_prompt_composer_defaults_to_analysis_not_listing() -> None:
    composer = PromptComposer()
    prompt_input = composer.build_prompt_input(
        language="pl",
        response_mode="assessment_plus_guidance",
        grounding=_base_grounding(),
        user_message="Jak jadlem w tym tygodniu?",
    )
    developer_payload = json.loads(composer.compose_messages(prompt_input)[1]["content"])
    assert developer_payload["antiListingPolicy"]["explicitListingRequested"] is False
    assert developer_payload["responseShape"] == "mixed_weekly_summary_and_goal"

    listing_prompt_input = composer.build_prompt_input(
        language="pl",
        response_mode="assessment_plus_guidance",
        grounding=_base_grounding(),
        user_message="Wypisz wszystkie posilki z tego tygodnia.",
    )
    listing_payload = json.loads(composer.compose_messages(listing_prompt_input)[1]["content"])
    assert listing_payload["antiListingPolicy"]["explicitListingRequested"] is True
    assert listing_payload["responseShape"] == "explicit_listing_request"


def test_prompt_composer_contains_low_coverage_wording_guidance() -> None:
    composer = PromptComposer()
    prompt_input = composer.build_prompt_input(
        language="pl",
        response_mode="assessment_plus_guidance",
        grounding=_base_grounding(),
        user_message="Ocen moj tydzien.",
    )
    developer_payload = json.loads(composer.compose_messages(prompt_input)[1]["content"])

    wording = developer_payload["dataQualityWording"]
    assert "Widze tylko czesc wpisow z tego tygodnia." in wording["preferredPolish"]
    assert "nie mam dostepu do pelnej historii" in wording["avoid"]


def test_prompt_composer_resolves_mixed_app_help_and_nutrition_shape() -> None:
    composer = PromptComposer()
    grounding = {
        "planner": {
            "taskType": "mixed_capability_answer",
            "responseMode": "assessment_plus_guidance",
            "needsFollowUp": False,
            "capabilities": [
                "get_app_help_context",
                "resolve_time_scope",
                "get_nutrition_period_summary",
            ],
        },
        "appHelpContext": {"answerFacts": ["f1"]},
        "nutritionSummary": {"loggingCoverage": {"coverageLevel": "medium"}},
    }
    prompt_input = composer.build_prompt_input(
        language="pl",
        response_mode="assessment_plus_guidance",
        grounding=grounding,
        user_message="Jak dziala chat i jak jadlem w tygodniu?",
    )

    payload = json.loads(composer.compose_messages(prompt_input)[1]["content"])
    assert payload["responseShape"] == "mixed_app_help_and_nutrition"


def test_prompt_composer_resolves_app_help_only_shape() -> None:
    composer = PromptComposer()
    grounding = {
        "planner": {
            "taskType": "app_help_only",
            "responseMode": "concise_answer",
            "needsFollowUp": False,
            "capabilities": ["get_app_help_context"],
        },
        "appHelpContext": {"answerFacts": ["f1", "f2"]},
    }
    prompt_input = composer.build_prompt_input(
        language="pl",
        response_mode="concise_answer",
        grounding=grounding,
        user_message="Jak działa chat w Fitaly i z czego korzysta?",
    )

    payload = json.loads(composer.compose_messages(prompt_input)[1]["content"])
    assert payload["responseShape"] == "app_help_only"
    assert payload["responseBlueprint"]["style"] == "system_specific_explainer"


def test_prompt_composer_refusal_helper() -> None:
    composer = PromptComposer()
    assert "Fitaly" in composer.build_refusal_response("pl")
    assert "Fitaly" in composer.build_refusal_response("en")
