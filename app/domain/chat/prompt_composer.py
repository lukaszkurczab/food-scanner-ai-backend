from __future__ import annotations

import json
from typing import Any

from app.schemas.ai_chat.prompt import PromptBuildInputDto

SYSTEM_PROMPT = (
    "You are Fitaly AI Chat v2 assistant. "
    "Use only grounded backend data from developer payload. "
    "Prefer concise product analysis over raw record listing. "
    "Treat coverage/reliability/signals as first-class evidence. "
    "If data is partial, communicate limited confidence clearly. "
    "Never invent missing facts. "
    "Never provide medical diagnosis or treatment."
)

_NUTRITION_CAPABILITIES = {
    "resolve_time_scope",
    "get_nutrition_period_summary",
    "compare_periods",
    "get_meal_logging_quality",
}

_WEEKLY_SCOPE_TYPES = {"calendar_week", "rolling_7d", "last_7d"}

_EXPLICIT_LISTING_MARKERS = (
    "wypisz",
    "lista",
    "listę",
    "dokladne posilki",
    "dokładne posiłki",
    "pokaz wszystkie posilki",
    "pokaż wszystkie posiłki",
    "szczegoly wpisow",
    "szczegóły wpisów",
    "show all meals",
    "list meals",
    "exact meals",
    "detailed entries",
)


class PromptComposer:
    def build_prompt_input(
        self,
        *,
        language: str,
        response_mode: str,
        grounding: dict,
        user_message: str,
    ) -> dict:
        payload = {
            "language": language if language in {"pl", "en"} else "pl",
            "responseMode": response_mode,
            "grounding": grounding,
            "userMessage": user_message,
        }
        dto = PromptBuildInputDto.model_validate(payload)
        return dto.model_dump(by_alias=True, exclude_none=True)

    def compose_messages(self, prompt_input: dict) -> list[dict[str, str]]:
        dto = PromptBuildInputDto.model_validate(prompt_input)
        grounding_payload = dto.grounding.model_dump(by_alias=True, exclude_none=True)

        explicit_listing_requested = self._is_explicit_listing_request(dto.user_message)
        response_shape = self._infer_response_shape(
            response_mode=dto.response_mode,
            grounding=grounding_payload,
            user_message=dto.user_message,
            explicit_listing_requested=explicit_listing_requested,
        )
        developer_payload = {
            "contract": "fitaly_chat_v2_grounded_response",
            "language": dto.language,
            "responseMode": dto.response_mode,
            "responseShape": response_shape,
            "grounding": grounding_payload,
            "responseBlueprint": self._response_blueprint(response_shape),
            "antiListingPolicy": {
                "explicitListingRequested": explicit_listing_requested,
                "defaultBehavior": "analysis_not_record_dump",
                "rule": (
                    "Do not list all meals/entries unless user explicitly asked "
                    "for listing details."
                ),
            },
            "dataQualityWording": {
                "preferredPolish": [
                    "Widze tylko czesc wpisow z tego tygodnia.",
                    "Na podstawie zapisow z tego tygodnia moge ocenic tylko fragment obrazu.",
                    "Dane sa niepelne, wiec ocena jest ograniczona.",
                ],
                "preferredEnglish": [
                    "I can see only part of this week's entries.",
                    "Based on this week's logs I can assess only part of the picture.",
                    "Data is incomplete, so confidence is limited.",
                ],
                "avoid": [
                    "nie mam dostepu do pelnej historii",
                    "i don't have access to full history",
                ],
            },
            "scopeCorrectionPolicy": {
                "rule": (
                    "If latest user message corrects previous time scope or intent, "
                    "follow latest correction over older context."
                ),
                "example": "Nie o dzis, tylko o caly tydzien -> use full-week scope.",
            },
            "rules": [
                "Use only facts from grounding payload.",
                "For analytical nutrition questions, answer in verdict-first structure.",
                "Analytical answer order is mandatory: verdict, coverage/data quality, 2-3 observations, practical next step, optional one follow-up question.",
                "Do not start analytical answers with meal entry enumeration.",
                "Coverage/reliability/signals must influence confidence and wording.",
                "With low/partial coverage, avoid confident reduction claims from logged kcal only.",
                "Goal-related questions require interpretation, not only target restatement.",
                "If planner marked out_of_scope, refuse briefly and redirect to supported scope.",
                "Do not output hidden reasoning or internal chain-of-thought.",
            ],
        }

        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "developer",
                "content": json.dumps(developer_payload, ensure_ascii=False),
            },
            {"role": "user", "content": dto.user_message},
        ]

    def build_refusal_response(self, language: str) -> str:
        if language == "en":
            return (
                "I can only help with Fitaly app usage, your in-app data, and nutrition topics."
            )
        return (
            "Mogę pomóc tylko w kwestiach Fitaly, Twoich danych w aplikacji oraz tematów żywieniowych."
        )

    def _infer_response_shape(
        self,
        *,
        response_mode: str,
        grounding: dict[str, Any],
        user_message: str,
        explicit_listing_requested: bool,
    ) -> str:
        planner = grounding.get("planner")
        planner_map = planner if isinstance(planner, dict) else {}
        task_type = str(planner_map.get("taskType") or "").strip()
        needs_follow_up = bool(planner_map.get("needsFollowUp"))
        capabilities_raw = planner_map.get("capabilities")
        capabilities = {
            item for item in capabilities_raw if isinstance(item, str)
        } if isinstance(capabilities_raw, list) else set()

        if task_type == "out_of_scope_refusal" or response_mode == "refusal_redirect":
            return "out_of_scope_refusal"
        if task_type == "follow_up_required" or needs_follow_up:
            return "follow_up_required"

        has_nutrition = bool(capabilities.intersection(_NUTRITION_CAPABILITIES))
        has_goal = "get_goal_context" in capabilities
        has_app_help = "get_app_help_context" in capabilities
        scope = grounding.get("scope")
        scope_type = (
            str(scope.get("type")).strip()
            if isinstance(scope, dict) and isinstance(scope.get("type"), str)
            else ""
        )

        if explicit_listing_requested and has_nutrition:
            return "explicit_listing_request"

        if has_app_help and has_nutrition:
            return "mixed_app_help_and_nutrition"
        if has_goal and has_nutrition:
            if scope_type in _WEEKLY_SCOPE_TYPES or "tydzien" in user_message.lower() or "week" in user_message.lower():
                return "mixed_weekly_summary_and_goal"
            return "mixed_nutrition_and_goal"
        if has_goal:
            return "goal_progress_feedback"

        message_lower = user_message.strip().lower()
        if "compare" in message_lower or "porown" in message_lower:
            return "pattern_analysis"

        if has_nutrition:
            analytical_markers = (
                "jak jad",
                "podsum",
                "ocen",
                "analiz",
                "how did i eat",
                "assess",
                "summary",
            )
            if any(marker in message_lower for marker in analytical_markers):
                return "history_summary"
            if response_mode == "comparison_plus_guidance":
                return "pattern_analysis"
            if response_mode == "assessment_plus_guidance":
                return "pattern_analysis"
            if scope_type in _WEEKLY_SCOPE_TYPES or "tydzien" in message_lower or "week" in message_lower:
                return "weekly_summary_analysis"
            return "history_summary"

        if has_app_help:
            return "app_help_only"

        if response_mode == "comparison_plus_guidance":
            return "pattern_analysis"
        if response_mode == "assessment_plus_guidance":
            return "pattern_analysis"
        return "weekly_summary_analysis"

    def _response_blueprint(self, response_shape: str) -> dict[str, Any]:
        if response_shape == "out_of_scope_refusal":
            return {
                "style": "short_refusal_redirect",
                "order": ["boundary", "supported_scope_examples"],
                "maxBullets": 2,
            }

        if response_shape == "follow_up_required":
            return {
                "style": "single_clarifying_question",
                "order": ["why_needed_short", "clarifying_question"],
                "maxSentences": 2,
            }

        if response_shape == "mixed_app_help_and_nutrition":
            return {
                "style": "two_track_answer",
                "order": [
                    "verdict",
                    "coverage_data_quality",
                    "nutrition_observations",
                    "app_help_specifics",
                    "practical_next_step",
                    "optional_focused_follow_up",
                ],
                "maxKeyObservations": 3,
            }

        if response_shape == "app_help_only":
            return {
                "style": "system_specific_explainer",
                "order": [
                    "direct_answer",
                    "how_it_works_backend_steps",
                    "data_sources_scope",
                    "one_practical_tip",
                ],
                "maxBullets": 4,
            }

        if response_shape == "explicit_listing_request":
            return {
                "style": "listing_allowed",
                "order": ["short_context", "requested_listing", "optional_next_step"],
                "maxItems": 10,
            }

        if response_shape in {
            "history_summary",
            "weekly_summary_analysis",
            "pattern_analysis",
            "goal_progress_feedback",
            "mixed_nutrition_and_goal",
            "mixed_weekly_summary_and_goal",
        }:
            return {
                "style": "verdict_first_product_analysis",
                "order": [
                    "verdict",
                    "coverage_data_quality",
                    "key_observations",
                    "practical_next_step",
                    "optional_focused_follow_up",
                ],
                "maxKeyObservations": 3,
                "goalAware": response_shape in {
                    "goal_progress_feedback",
                    "mixed_nutrition_and_goal",
                    "mixed_weekly_summary_and_goal",
                },
                "mustIncludeCoverageEarly": True,
            }

        return {
            "style": "concise_answer",
            "order": ["verdict", "practical_next_step"],
        }

    @staticmethod
    def _is_explicit_listing_request(user_message: str) -> bool:
        lowered = user_message.strip().lower()
        return any(marker in lowered for marker in _EXPLICIT_LISTING_MARKERS)
