"""Wrapper around OpenAI chat completions used by the API layer.

The `ask_chat` function is intentionally small so route and service tests can
mock it directly instead of touching the external OpenAI SDK.
"""

import asyncio
import logging

import openai

from app.core.config import settings
from app.core.exceptions import OpenAIServiceError

logger = logging.getLogger(__name__)
openai.api_key = settings.OPENAI_API_KEY
OPENAI_ERROR = getattr(getattr(openai, "error", openai), "OpenAIError")


async def ask_chat(
    message: str,
    model: str = "gpt-3.5-turbo",
    timeout: int = 30,
) -> str:
    """Send one user message to OpenAI and return the assistant reply."""
    if not settings.OPENAI_API_KEY:
        raise OpenAIServiceError("OpenAI API key is not configured.")

    try:
        response = await asyncio.wait_for(
            openai.ChatCompletion.acreate(
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

    choices = response["choices"] if isinstance(response, dict) else response.choices
    if not choices:
        raise OpenAIServiceError("OpenAI returned an empty response.")

    first_choice = choices[0]
    if isinstance(first_choice, dict):
        reply = first_choice.get("message", {}).get("content")
    else:
        reply = getattr(first_choice.message, "content", None)

    if not reply:
        raise OpenAIServiceError("OpenAI returned an empty response.")

    return reply.strip()
