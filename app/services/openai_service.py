"""Wrapper around OpenAI chat completions used by the API layer.

The `ask_chat` function is intentionally small so route and service tests can
mock it directly instead of touching the external OpenAI SDK.
"""

import asyncio
import json
import logging
import re
from typing import Any, TypedDict
from typing import cast

try:
    from typing import NotRequired
except ImportError:  # pragma: no cover - Python < 3.11 compatibility
    from typing_extensions import NotRequired

import openai
from openai.types.chat import ChatCompletionMessageParam

from app.core.config import settings
from app.core.exceptions import OpenAIServiceError

logger = logging.getLogger(__name__)
OPENAI_ERROR = getattr(openai, "OpenAIError")


class AnalyzedIngredient(TypedDict):
    name: str
    amount: float
    protein: float
    fat: float
    carbs: float
    kcal: float
    unit: NotRequired[str]


class OpenAIUsage(TypedDict):
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None


class ChatCompletionResult(TypedDict):
    content: str
    usage: OpenAIUsage


class PhotoAnalysisResult(TypedDict):
    ingredients: list[AnalyzedIngredient]
    usage: OpenAIUsage


def _as_object_map(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    raw_map = cast(dict[object, object], value)
    result: dict[str, object] = {}
    for raw_key, raw_item in raw_map.items():
        if isinstance(raw_key, str):
            result[raw_key] = raw_item
    return result


def _as_object_list(value: object) -> list[object] | None:
    if not isinstance(value, list):
        return None
    values = cast(list[object], value)
    return values


def _resolve_chat_messages(message: str) -> list[ChatCompletionMessageParam]:
    marker = "\nUSER_MESSAGE="
    marker_index = message.rfind(marker)
    direct_user_message: ChatCompletionMessageParam = {
        "role": "user",
        "content": message,
    }
    if marker_index == -1:
        return [direct_user_message]

    system_prompt = message[:marker_index].strip()
    user_message = message[marker_index + len(marker) :].strip()
    if not system_prompt or not user_message:
        return [direct_user_message]

    system_message: ChatCompletionMessageParam = {
        "role": "system",
        "content": system_prompt,
    }
    user_message_param: ChatCompletionMessageParam = {
        "role": "user",
        "content": user_message,
    }

    return [
        system_message,
        user_message_param,
    ]


def _extract_reply_content(response: Any) -> str:
    response_obj = cast(object, response)
    if isinstance(response, dict):
        response_map = _as_object_map(cast(object, response)) or {}
        choices_raw = response_map.get("choices")
    else:
        choices_raw = cast(object, getattr(response_obj, "choices", None))
    choices = _as_object_list(choices_raw)
    if not choices:
        raise OpenAIServiceError("OpenAI returned an empty response.")

    first_choice = choices[0]
    reply: str | None = None
    if isinstance(first_choice, dict):
        choice_map = _as_object_map(cast(object, first_choice)) or {}
        message_map = _as_object_map(choice_map.get("message")) or {}
        raw_reply = message_map.get("content")
        if isinstance(raw_reply, str):
            reply = raw_reply
    else:
        first_choice_obj = cast(object, first_choice)
        message = cast(object, getattr(first_choice_obj, "message", None))
        raw_reply = cast(object, getattr(message, "content", None))
        if isinstance(raw_reply, str):
            reply = raw_reply

    if not reply:
        raise OpenAIServiceError("OpenAI returned an empty response.")

    return reply.strip()


def _extract_usage(response: Any) -> OpenAIUsage:
    response_obj = cast(object, response)
    if isinstance(response, dict):
        response_map = _as_object_map(cast(object, response)) or {}
        usage_raw = response_map.get("usage")
    else:
        usage_raw = cast(object, getattr(response_obj, "usage", None))
    if usage_raw is None:
        return {
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
        }

    if isinstance(usage_raw, dict):
        usage_map = _as_object_map(cast(object, usage_raw)) or {}
        prompt_tokens = usage_map.get("prompt_tokens")
        completion_tokens = usage_map.get("completion_tokens")
        total_tokens = usage_map.get("total_tokens")
    else:
        usage_obj = cast(object, usage_raw)
        prompt_tokens = cast(object, getattr(usage_obj, "prompt_tokens", None))
        completion_tokens = cast(object, getattr(usage_obj, "completion_tokens", None))
        total_tokens = cast(object, getattr(usage_obj, "total_tokens", None))

    def _to_int(value: object) -> int | None:
        if isinstance(value, bool):
            return None
        return int(value) if isinstance(value, (int, float)) else None

    return {
        "prompt_tokens": _to_int(prompt_tokens),
        "completion_tokens": _to_int(completion_tokens),
        "total_tokens": _to_int(total_tokens),
    }


def _parse_json_array(raw: str) -> list[object]:
    start = raw.find("[")
    end = raw.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise OpenAIServiceError("OpenAI returned an invalid ingredients payload.")

    array_text = raw[start : end + 1]

    try:
        parsed = json.loads(array_text)
    except json.JSONDecodeError:
        cleaned = array_text.replace(",]", "]").replace(",}", "}")
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise OpenAIServiceError(
                "OpenAI returned an invalid ingredients payload."
            ) from exc

    if not isinstance(parsed, list):
        raise OpenAIServiceError("OpenAI returned an invalid ingredients payload.")

    parsed_list = cast(list[object], parsed)
    return parsed_list


def _coerce_number(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            cleaned = re.sub(r"[^0-9.+-]", "", value)
            if cleaned:
                try:
                    return float(cleaned)
                except ValueError as exc:
                    raise OpenAIServiceError(
                        "OpenAI returned an invalid ingredient number."
                    ) from exc
            raise OpenAIServiceError("OpenAI returned an invalid ingredient number.")
    raise OpenAIServiceError("OpenAI returned an invalid ingredient number.")


def parse_ingredients_reply(raw: str) -> list[AnalyzedIngredient]:
    parsed = _parse_json_array(raw)
    ingredients: list[AnalyzedIngredient] = []

    for raw_item in parsed:
        item = _as_object_map(cast(object, raw_item))
        if item is None:
            raise OpenAIServiceError("OpenAI returned an invalid ingredient object.")

        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            raise OpenAIServiceError("OpenAI returned an invalid ingredient name.")

        amount = _coerce_number(item.get("amount"))
        protein = _coerce_number(item.get("protein"))
        fat = _coerce_number(item.get("fat"))
        carbs = _coerce_number(item.get("carbs"))
        if amount <= 0:
            raise OpenAIServiceError("OpenAI returned an invalid ingredient amount.")

        kcal_value = item.get("kcal")
        try:
            kcal = _coerce_number(kcal_value)
        except OpenAIServiceError:
            kcal = protein * 4 + carbs * 4 + fat * 9

        ingredient: AnalyzedIngredient = {
            "name": name.strip(),
            "amount": amount,
            "protein": protein,
            "fat": fat,
            "carbs": carbs,
            "kcal": kcal,
        }

        unit = item.get("unit")
        if isinstance(unit, str) and unit.strip():
            ingredient["unit"] = unit.strip()

        ingredients.append(ingredient)

    if not ingredients:
        raise OpenAIServiceError("OpenAI returned an empty ingredients list.")

    return ingredients


async def ask_chat(
    message: str,
    model: str = "gpt-4o-mini",
    timeout: int = 30,
) -> str:
    """Send one user message to OpenAI and return the assistant reply."""
    result = await ask_chat_completion(message, model=model, timeout=timeout)
    return result["content"]


async def ask_chat_completion(
    message: str,
    model: str = "gpt-4o-mini",
    timeout: int = 30,
) -> ChatCompletionResult:
    """Send one user message to OpenAI and return reply plus usage metadata."""
    if not settings.OPENAI_API_KEY:
        raise OpenAIServiceError("OpenAI API key is not configured.")

    client = openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY, timeout=timeout)
    messages = _resolve_chat_messages(message)

    try:
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.2,
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError as exc:
        logger.exception("OpenAI request timed out.")
        raise OpenAIServiceError("OpenAI request timed out.") from exc
    except OPENAI_ERROR as exc:
        logger.exception("OpenAI request failed.")
        raise OpenAIServiceError("OpenAI request failed.") from exc

    return {
        "content": _extract_reply_content(response),
        "usage": _extract_usage(response),
    }


async def analyze_photo(
    image_base64: str,
    lang: str = "en",
    model: str = "gpt-4o",
    timeout: int = 30,
) -> list[AnalyzedIngredient]:
    """Analyze a meal photo and return normalized ingredient entries."""
    result = await analyze_photo_completion(
        image_base64,
        lang=lang,
        model=model,
        timeout=timeout,
    )
    return result["ingredients"]


async def analyze_photo_completion(
    image_base64: str,
    lang: str = "en",
    model: str = "gpt-4o",
    timeout: int = 30,
) -> PhotoAnalysisResult:
    """Analyze a meal photo and return normalized ingredient entries plus usage."""
    if not settings.OPENAI_API_KEY:
        raise OpenAIServiceError("OpenAI API key is not configured.")

    client = openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY, timeout=timeout)

    prompt = (
        "You extract simplified nutrition data from meal photos. "
        f"Return names in {lang}. Return ONLY a raw JSON array. "
        'Schema per item: {"name":"string","amount":123,"protein":0,"fat":0,"carbs":0,"kcal":0,"unit":"ml"}. '
        "The unit key is optional and only for liquids. Use grams by default. "
        "Prefer one combined item for a ready-made dish unless foods are clearly separate. "
        "Do not include markdown or explanation."
    )

    try:
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{image_base64}",
                                },
                            },
                        ],
                    }
                ],
                temperature=0.1,
                max_tokens=600,
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError as exc:
        logger.exception("OpenAI photo analysis timed out.")
        raise OpenAIServiceError("OpenAI photo analysis timed out.") from exc
    except OPENAI_ERROR as exc:
        logger.exception("OpenAI photo analysis failed.")
        raise OpenAIServiceError("OpenAI photo analysis failed.") from exc

    reply = _extract_reply_content(response)
    return {
        "ingredients": parse_ingredients_reply(reply),
        "usage": _extract_usage(response),
    }
