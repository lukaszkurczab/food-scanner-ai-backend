"""Wrapper around OpenAI chat completions used by the API layer.

The `ask_chat` function is intentionally small so route and service tests can
mock it directly instead of touching the external OpenAI SDK.
"""

import asyncio
import json
import logging
import re
from typing import Any, NotRequired, TypedDict
from typing import cast

import openai

from app.core.config import settings
from app.core.exceptions import OpenAIServiceError

logger = logging.getLogger(__name__)
openai.api_key = settings.OPENAI_API_KEY
OPENAI_ERROR = getattr(openai, "OpenAIError")


class AnalyzedIngredient(TypedDict):
    name: str
    amount: float
    protein: float
    fat: float
    carbs: float
    kcal: float
    unit: NotRequired[str]


def _extract_reply_content(response: Any) -> str:
    choices = response["choices"] if isinstance(response, dict) else response.choices
    if not choices:
        raise OpenAIServiceError("OpenAI returned an empty response.")

    first_choice = choices[0]
    if isinstance(first_choice, dict):
        reply = first_choice.get("message", {}).get("content")
    else:
        first_choice_obj = cast(object, first_choice)
        message = cast(Any, getattr(first_choice_obj, "message", None))
        reply = cast(str | None, getattr(cast(object, message), "content", None))

    if not reply:
        raise OpenAIServiceError("OpenAI returned an empty response.")

    return reply.strip()


def _parse_json_array(raw: str) -> list[Any]:
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

    return parsed


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

    for item in parsed:
        if not isinstance(item, dict):
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
    if not settings.OPENAI_API_KEY:
        raise OpenAIServiceError("OpenAI API key is not configured.")

    client = openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY, timeout=timeout)

    try:
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": message}],
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

    return _extract_reply_content(response)


async def analyze_photo(
    image_base64: str,
    lang: str = "en",
    model: str = "gpt-4o",
    timeout: int = 30,
) -> list[AnalyzedIngredient]:
    """Analyze a meal photo and return normalized ingredient entries."""
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
    return parse_ingredients_reply(reply)
