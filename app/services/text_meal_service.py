"""Backend-owned text meal analysis helpers."""

import json
from typing import TypedDict

from app.core.exceptions import OpenAIServiceError
from app.schemas.ai_text_meal import AiTextMealPayload
from app.services import openai_service


class TextMealAnalysisResult(TypedDict):
    ingredients: list[openai_service.AnalyzedIngredient]
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None


def _none_if_blank(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def build_text_meal_prompt(payload: AiTextMealPayload, lang: str) -> str:
    normalized_payload: dict[str, object | None] = {
        "name": _none_if_blank(payload.name),
        "ingredients": _none_if_blank(payload.ingredients),
        "amount_g": payload.amount_g,
        "notes": _none_if_blank(payload.notes),
        "lang": lang,
    }
    return (
        f"You are a nutrition assistant. The user language is {lang}. "
        "Analyze the provided JSON payload describing a meal and return ONLY a raw JSON array. "
        'Each item must use this exact schema: {"name":"string","amount":123,"protein":0,"fat":0,"carbs":0,"kcal":0,"unit":"ml"}. '
        "The unit key is optional and only for liquids. "
        "Amount must be in grams or ml, numbers only, with no prose, markdown, or explanation. "
        "Estimate realistic nutrition values for the provided amount. "
        "Never return all nutrition values as 0 unless the item is explicitly water. "
        "Treat a prepared dish as ONE item unless clearly separate foods are described. "
        "Convert household measures to grams/ml when possible. "
        "Names must be in the user's language from the payload. "
        f"Payload: {json.dumps(normalized_payload, ensure_ascii=False)}"
    )


def _has_non_zero_nutrition(
    ingredients: list[openai_service.AnalyzedIngredient],
) -> bool:
    for ingredient in ingredients:
        if (
            ingredient["kcal"] > 0
            or ingredient["protein"] > 0
            or ingredient["fat"] > 0
            or ingredient["carbs"] > 0
        ):
            return True
    return False


def build_text_meal_retry_prompt(payload: AiTextMealPayload, lang: str) -> str:
    return (
        f"{build_text_meal_prompt(payload, lang)} "
        "Your previous answer was invalid. "
        "Return a corrected JSON array where at least one nutrition value is > 0 "
        "for non-water items."
    )


async def analyze_text_meal(
    payload: AiTextMealPayload,
    *,
    lang: str = "en",
) -> list[openai_service.AnalyzedIngredient]:
    result = await analyze_text_meal_with_usage(payload, lang=lang)
    return result["ingredients"]


async def analyze_text_meal_with_usage(
    payload: AiTextMealPayload,
    *,
    lang: str = "en",
) -> TextMealAnalysisResult:
    prompt = build_text_meal_prompt(payload, lang)
    first_pass = await openai_service.ask_chat_completion(prompt)
    ingredients = openai_service.parse_ingredients_reply(first_pass["content"])
    if _has_non_zero_nutrition(ingredients):
        usage = first_pass["usage"]
        return {
            "ingredients": ingredients,
            "prompt_tokens": usage["prompt_tokens"],
            "completion_tokens": usage["completion_tokens"],
            "total_tokens": usage["total_tokens"],
        }

    retry_prompt = build_text_meal_retry_prompt(payload, lang)
    retry_pass = await openai_service.ask_chat_completion(retry_prompt)
    retry_ingredients = openai_service.parse_ingredients_reply(retry_pass["content"])
    if _has_non_zero_nutrition(retry_ingredients):
        first_usage = first_pass["usage"]
        retry_usage = retry_pass["usage"]

        def _sum_usage(first: int | None, second: int | None) -> int | None:
            if first is None and second is None:
                return None
            return int(first or 0) + int(second or 0)

        return {
            "ingredients": retry_ingredients,
            "prompt_tokens": _sum_usage(
                first_usage["prompt_tokens"],
                retry_usage["prompt_tokens"],
            ),
            "completion_tokens": _sum_usage(
                first_usage["completion_tokens"],
                retry_usage["completion_tokens"],
            ),
            "total_tokens": _sum_usage(
                first_usage["total_tokens"],
                retry_usage["total_tokens"],
            ),
        }

    raise OpenAIServiceError(
        "OpenAI returned ingredients without nutrition values."
    )
