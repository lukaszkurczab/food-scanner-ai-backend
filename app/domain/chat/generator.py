from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from app.core.exceptions import OpenAIServiceError


@dataclass(frozen=True)
class GenerationUsage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass(frozen=True)
class GenerationResult:
    text: str
    usage: GenerationUsage


@dataclass(frozen=True)
class _GenerationContract:
    language: str
    response_shape: str | None
    explicit_listing_requested: bool
    coverage_level: str | None


class _StructuredAnalyticalAnswerDto(BaseModel):
    verdict: str
    coverage_statement: str = Field(alias="coverageStatement")
    key_observations: list[str] = Field(alias="keyObservations", min_length=1, max_length=3)
    practical_next_step: str = Field(alias="practicalNextStep")
    follow_up_question: str | None = Field(default=None, alias="followUpQuestion")


_ANALYTICAL_RESPONSE_SHAPES = {
    "history_summary",
    "weekly_summary_analysis",
    "pattern_analysis",
    "goal_progress_feedback",
    "mixed_nutrition_and_goal",
    "mixed_weekly_summary_and_goal",
    "mixed_app_help_and_nutrition",
}

_GOAL_ORIENTED_SHAPES = {
    "goal_progress_feedback",
    "mixed_nutrition_and_goal",
    "mixed_weekly_summary_and_goal",
}


class ChatGenerator:
    def __init__(
        self,
        openai_client: Any,
        *,
        model: str = "gpt-4o-mini",
        temperature: float = 0.2,
    ) -> None:
        self.openai_client = openai_client
        self.model = model
        self.temperature = temperature

    async def generate(self, *, messages: list[dict[str, str]]) -> GenerationResult:
        contract = self._extract_generation_contract(messages)
        if (
            contract.response_shape in _ANALYTICAL_RESPONSE_SHAPES
            and not contract.explicit_listing_requested
        ):
            return await self._generate_structured_analytical_answer(
                messages=messages,
                contract=contract,
            )
        return await self._generate_plain_text(messages=messages)

    async def _generate_plain_text(self, *, messages: list[dict[str, str]]) -> GenerationResult:
        response = await self.openai_client.chat_completion(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
        )
        text = str(response.get("content") or "").strip()
        if not text:
            raise OpenAIServiceError("OpenAI returned an empty completion.")

        usage_raw = response.get("usage") if isinstance(response, dict) else None
        usage = GenerationUsage(
            prompt_tokens=self._to_int(usage_raw, "prompt_tokens"),
            completion_tokens=self._to_int(usage_raw, "completion_tokens"),
            total_tokens=self._to_int(usage_raw, "total_tokens"),
        )
        return GenerationResult(text=text, usage=usage)

    async def _generate_structured_analytical_answer(
        self,
        *,
        messages: list[dict[str, str]],
        contract: _GenerationContract,
    ) -> GenerationResult:
        usage_raw: Any = {}
        payload: Any | None = None

        if hasattr(self.openai_client, "responses_json_with_usage"):
            structured = await self.openai_client.responses_json_with_usage(
                model=self.model,
                messages=messages,
                schema=_StructuredAnalyticalAnswerDto,
                temperature=min(self.temperature, 0.2),
            )
            if isinstance(structured, dict):
                payload = structured.get("data")
                usage_raw = structured.get("usage") or {}
        elif hasattr(self.openai_client, "responses_json"):
            payload = await self.openai_client.responses_json(
                model=self.model,
                messages=messages,
                schema=_StructuredAnalyticalAnswerDto,
                temperature=min(self.temperature, 0.2),
            )
        else:
            return await self._generate_plain_text(messages=messages)

        if payload is None:
            raise OpenAIServiceError("OpenAI returned an empty structured analytical response.")

        structured_answer = _StructuredAnalyticalAnswerDto.model_validate(payload)
        text = self._render_analytical_answer(
            answer=structured_answer,
            contract=contract,
        )
        if not text:
            raise OpenAIServiceError("OpenAI returned an empty completion.")

        usage = GenerationUsage(
            prompt_tokens=self._to_int(usage_raw, "prompt_tokens"),
            completion_tokens=self._to_int(usage_raw, "completion_tokens"),
            total_tokens=self._to_int(usage_raw, "total_tokens"),
        )
        return GenerationResult(text=text, usage=usage)

    def _render_analytical_answer(
        self,
        *,
        answer: _StructuredAnalyticalAnswerDto,
        contract: _GenerationContract,
    ) -> str:
        verdict = self._clean_sentence(answer.verdict)
        coverage = self._clean_sentence(answer.coverage_statement)
        coverage = self._enforce_coverage_statement(
            coverage=coverage,
            contract=contract,
        )
        verdict = self._enforce_goal_caution(verdict=verdict, contract=contract)

        observations = [
            cleaned
            for cleaned in (
                self._sanitize_observation(item) for item in answer.key_observations
            )
            if cleaned
        ]
        observations = observations[:3] if observations else [self._fallback_observation(contract)]

        next_step = self._clean_sentence(answer.practical_next_step)
        follow_up = self._clean_sentence(answer.follow_up_question or "")

        if contract.language == "en":
            sections = [
                f"Verdict: {verdict}",
                f"Data quality: {coverage}",
                "Key observations:\n" + "\n".join(f"- {item}" for item in observations),
                f"Practical next step: {next_step}",
            ]
            if follow_up:
                sections.append(f"Optional follow-up: {follow_up}")
            return "\n\n".join(sections).strip()

        sections = [
            f"Werdykt: {verdict}",
            f"Jakosc danych: {coverage}",
            "Najwazniejsze obserwacje:\n" + "\n".join(f"- {item}" for item in observations),
            f"Praktyczny nastepny krok: {next_step}",
        ]
        if follow_up:
            sections.append(f"Opcjonalne pytanie doprecyzowujace: {follow_up}")
        return "\n\n".join(sections).strip()

    def _extract_generation_contract(self, messages: list[dict[str, str]]) -> _GenerationContract:
        language = "pl"
        response_shape: str | None = None
        explicit_listing_requested = False
        coverage_level: str | None = None

        for message in messages:
            if str(message.get("role") or "") != "developer":
                continue
            try:
                payload = json.loads(str(message.get("content") or ""))
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue

            maybe_language = payload.get("language")
            if maybe_language == "en":
                language = "en"

            maybe_shape = payload.get("responseShape")
            if isinstance(maybe_shape, str) and maybe_shape.strip():
                response_shape = maybe_shape.strip()

            anti_listing = payload.get("antiListingPolicy")
            if isinstance(anti_listing, dict):
                explicit_listing_requested = bool(anti_listing.get("explicitListingRequested"))

            grounding = payload.get("grounding")
            if isinstance(grounding, dict):
                coverage_level = self._extract_coverage_level_from_grounding(grounding)

            break

        return _GenerationContract(
            language=language,
            response_shape=response_shape,
            explicit_listing_requested=explicit_listing_requested,
            coverage_level=coverage_level,
        )

    @staticmethod
    def _extract_coverage_level_from_grounding(grounding: dict[str, Any]) -> str | None:
        nutrition = grounding.get("nutritionSummary")
        if isinstance(nutrition, dict):
            coverage = nutrition.get("loggingCoverage")
            if isinstance(coverage, dict):
                level = coverage.get("coverageLevel")
                if isinstance(level, str) and level.strip():
                    return level.strip().lower()

        quality = grounding.get("mealLoggingQuality")
        if isinstance(quality, dict):
            level = quality.get("coverageLevel")
            if isinstance(level, str) and level.strip():
                return level.strip().lower()
        return None

    def _enforce_coverage_statement(
        self,
        *,
        coverage: str,
        contract: _GenerationContract,
    ) -> str:
        level = (contract.coverage_level or "").lower()
        normalized = coverage.strip()

        if level in {"none", "low"}:
            if contract.language == "en":
                caution_markers = ("partial", "incomplete", "limited", "not complete")
                if any(marker in normalized.lower() for marker in caution_markers):
                    return normalized
                return (
                    "I can see only part of the logged entries, so confidence is limited and "
                    "this is only a partial view."
                )

            caution_markers_pl = ("czesc", "część", "niepel", "niepeł", "ograniczon")
            if any(marker in normalized.lower() for marker in caution_markers_pl):
                return normalized
            return (
                "Widze tylko czesc zapisow, wiec ocena jest ograniczona i dotyczy jedynie "
                "fragmentu obrazu."
            )

        if level == "medium" and contract.language == "pl":
            if not normalized:
                return "Dane sa czesciowe, wiec pewnosc oceny jest umiarkowana."
        if level == "medium" and contract.language == "en":
            if not normalized:
                return "Data is partial, so confidence is moderate."

        return normalized or (
            "Dane sa wystarczajace do ostroznej oceny."
            if contract.language == "pl"
            else "Data is sufficient for a cautious assessment."
        )

    def _enforce_goal_caution(self, *, verdict: str, contract: _GenerationContract) -> str:
        if contract.response_shape not in _GOAL_ORIENTED_SHAPES:
            return verdict

        if (contract.coverage_level or "").lower() not in {"none", "low"}:
            return verdict

        lowered = verdict.lower()
        if contract.language == "en":
            if any(marker in lowered for marker in ("partial", "limited", "incomplete", "preliminary")):
                return verdict
            return f"Preliminary view only: data is partial. {verdict}".strip()

        if any(marker in lowered for marker in ("wstep", "część", "czesc", "niepel", "ograniczon")):
            return verdict
        return f"To wstepna ocena, bo dane sa niepelne. {verdict}".strip()

    @staticmethod
    def _sanitize_observation(value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        text = re.sub(r"^\s*(?:[-*•]|\d+[\.)])\s*", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    @staticmethod
    def _clean_sentence(value: str) -> str:
        text = str(value or "").strip()
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    @staticmethod
    def _fallback_observation(contract: _GenerationContract) -> str:
        if contract.language == "en":
            return "Logged data is enough only for a focused, partial assessment."
        return "Zapisane dane pozwalaja tylko na czesciowa, ukierunkowana ocene."

    @staticmethod
    def _to_int(usage: Any, key: str) -> int:
        if isinstance(usage, dict):
            value = usage.get(key)
            if isinstance(value, bool):
                return 0
            if isinstance(value, (int, float)):
                return int(value)
        return 0
