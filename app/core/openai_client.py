from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

import openai
from pydantic import BaseModel, TypeAdapter

from app.core.config import settings
from app.core.exceptions import OpenAIServiceError


def _as_object_map(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    result: dict[str, object] = {}
    for raw_key, raw_value in value.items():
        if isinstance(raw_key, str):
            result[raw_key] = raw_value
    return result


def _as_object_list(value: object) -> list[object] | None:
    if not isinstance(value, list):
        return None
    return value


def _schema_name(schema: Any) -> str:
    if isinstance(schema, type):
        return schema.__name__.lower()
    return "structured_output"


def _schema_json(schema: Any) -> dict[str, Any]:
    if isinstance(schema, type) and issubclass(schema, BaseModel):
        return schema.model_json_schema()
    return TypeAdapter(schema).json_schema()


def _validate_against_schema(schema: Any, payload: Any) -> Any:
    if isinstance(schema, type) and issubclass(schema, BaseModel):
        model = schema.model_validate(payload)
        return model.model_dump(by_alias=True)
    return TypeAdapter(schema).validate_python(payload)


def _extract_reply_content(response: Any) -> str:
    response_obj = response
    if isinstance(response_obj, dict):
        response_map = _as_object_map(response_obj) or {}
        choices_raw = response_map.get("choices")
    else:
        choices_raw = getattr(response_obj, "choices", None)

    choices = _as_object_list(choices_raw)
    if not choices:
        raise OpenAIServiceError("OpenAI returned an empty response.")

    first_choice = choices[0]
    if isinstance(first_choice, dict):
        choice_map = _as_object_map(first_choice) or {}
        message_map = _as_object_map(choice_map.get("message")) or {}
        content = message_map.get("content")
    else:
        message = getattr(first_choice, "message", None)
        content = getattr(message, "content", None)

    if not isinstance(content, str) or not content.strip():
        raise OpenAIServiceError("OpenAI returned an empty response.")
    return content.strip()


def _to_optional_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return None


def _extract_usage(response: Any) -> dict[str, int | None]:
    response_obj = response
    if isinstance(response_obj, dict):
        usage_raw = (_as_object_map(response_obj) or {}).get("usage")
    else:
        usage_raw = getattr(response_obj, "usage", None)

    if usage_raw is None:
        return {
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
        }

    if isinstance(usage_raw, dict):
        usage_map = _as_object_map(usage_raw) or {}
        prompt_tokens = usage_map.get("prompt_tokens")
        completion_tokens = usage_map.get("completion_tokens")
        total_tokens = usage_map.get("total_tokens")
    else:
        prompt_tokens = getattr(usage_raw, "prompt_tokens", None)
        completion_tokens = getattr(usage_raw, "completion_tokens", None)
        total_tokens = getattr(usage_raw, "total_tokens", None)

    return {
        "prompt_tokens": _to_optional_int(prompt_tokens),
        "completion_tokens": _to_optional_int(completion_tokens),
        "total_tokens": _to_optional_int(total_tokens),
    }


class OpenAIClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        timeout: float = 20.0,
        client: Any | None = None,
    ) -> None:
        if client is not None:
            self._client = client
            return

        resolved_api_key = (api_key or settings.OPENAI_API_KEY).strip()
        if not resolved_api_key:
            raise OpenAIServiceError("OpenAI API key is not configured.")
        self._client = openai.AsyncOpenAI(api_key=resolved_api_key, timeout=timeout)

    async def responses_json(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        schema: Any,
        temperature: float = 0.0,
    ) -> Any:
        structured = await self.responses_json_with_usage(
            model=model,
            messages=messages,
            schema=schema,
            temperature=temperature,
        )
        if isinstance(structured, dict):
            return structured.get("data")
        return structured

    async def responses_json_with_usage(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        schema: Any,
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        try:
            response = await self._client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": _schema_name(schema),
                        "schema": _schema_json(schema),
                        "strict": True,
                    },
                },
            )
        except Exception as exc:  # noqa: BLE001
            raise OpenAIServiceError("OpenAI structured request failed.") from exc

        try:
            content = response.choices[0].message.content
        except Exception as exc:  # noqa: BLE001
            raise OpenAIServiceError("OpenAI returned an empty structured response.") from exc

        if not isinstance(content, str) or not content.strip():
            raise OpenAIServiceError("OpenAI returned an empty structured response.")

        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise OpenAIServiceError("OpenAI returned invalid JSON response.") from exc

        try:
            data = _validate_against_schema(schema, payload)
        except Exception as exc:  # noqa: BLE001
            raise OpenAIServiceError("OpenAI returned schema-invalid response.") from exc

        return {
            "data": data,
            "usage": _extract_usage(response),
        }

    async def chat_completion(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
    ) -> dict[str, Any]:
        try:
            response = await self._client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
            )
        except Exception as exc:  # noqa: BLE001
            raise OpenAIServiceError("OpenAI completion request failed.") from exc

        return {
            "content": _extract_reply_content(response),
            "usage": _extract_usage(response),
        }


@lru_cache(maxsize=1)
def get_openai_client() -> OpenAIClient:
    return OpenAIClient()
