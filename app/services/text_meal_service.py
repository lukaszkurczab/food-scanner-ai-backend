"""Backend-owned text meal analysis helpers."""

import json

from app.schemas.ai_text_meal import AiTextMealPayload
from app.services import openai_service


def _none_if_blank(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def build_text_meal_prompt(payload: AiTextMealPayload, lang: str) -> str:
    normalized_payload = {
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
        "Treat a prepared dish as ONE item unless clearly separate foods are described. "
        "Convert household measures to grams/ml when possible. "
        "Names must be in the user's language from the payload. "
        f"Payload: {json.dumps(normalized_payload, ensure_ascii=False)}"
    )


async def analyze_text_meal(
    payload: AiTextMealPayload,
    *,
    lang: str = "en",
) -> list[openai_service.AnalyzedIngredient]:
    prompt = build_text_meal_prompt(payload, lang)
    reply = await openai_service.ask_chat(prompt)
    return openai_service.parse_ingredients_reply(reply)
