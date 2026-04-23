from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest

from app.core.exceptions import OpenAIServiceError
from app.core.openai_client import OpenAIClient
from app.schemas.ai_chat.planner import PlannerResultDto


@dataclass
class _Message:
    content: str


@dataclass
class _Choice:
    message: _Message


@dataclass
class _Usage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass
class _Response:
    choices: list[_Choice]
    usage: _Usage


class _BadRequest(Exception):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class _FakeCompletions:
    def __init__(self, calls: list[dict[str, Any]], *, mode: str) -> None:
        self.calls = calls
        self.mode = mode

    async def create(self, **kwargs: Any) -> _Response:
        self.calls.append(kwargs)

        strict = bool(kwargs["response_format"]["json_schema"]["strict"])
        if self.mode == "strict_fallback" and strict:
            raise _BadRequest(
                "Invalid schema for response_format 'plannerresultdto': additionalProperties"
            )
        if self.mode == "strict_error_only":
            raise _BadRequest("Invalid request payload.")

        payload = {
            "taskType": "out_of_scope_refusal",
            "queryUnderstanding": {
                "requiresUserData": False,
                "requestedScopeLabel": None,
                "mixedRequest": False,
                "topics": ["scope"],
            },
            "capabilities": [],
            "responseMode": "refusal_redirect",
            "needsFollowUp": False,
            "followUpQuestion": None,
        }
        return _Response(
            choices=[_Choice(message=_Message(content=json.dumps(payload)))],
            usage=_Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )


class _FakeChat:
    def __init__(self, calls: list[dict[str, Any]], *, mode: str) -> None:
        self.completions = _FakeCompletions(calls, mode=mode)


class _FakeClient:
    def __init__(self, calls: list[dict[str, Any]], *, mode: str) -> None:
        self.chat = _FakeChat(calls, mode=mode)


async def test_openai_client_falls_back_to_non_strict_json_schema_on_schema_reject() -> None:
    calls: list[dict[str, Any]] = []
    client = OpenAIClient(client=_FakeClient(calls, mode="strict_fallback"))

    result = await client.responses_json_with_usage(
        model="gpt-4o-mini",
        messages=[{"role": "system", "content": "x"}],
        schema=PlannerResultDto,
        temperature=0.0,
    )

    assert result["data"]["taskType"] == "out_of_scope_refusal"
    assert result["usage"] == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
    }
    assert len(calls) == 2
    assert calls[0]["response_format"]["json_schema"]["strict"] is True
    assert calls[1]["response_format"]["json_schema"]["strict"] is False


async def test_openai_client_does_not_fallback_for_non_schema_bad_request() -> None:
    calls: list[dict[str, Any]] = []
    client = OpenAIClient(client=_FakeClient(calls, mode="strict_error_only"))

    with pytest.raises(OpenAIServiceError):
        await client.responses_json_with_usage(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": "x"}],
            schema=PlannerResultDto,
            temperature=0.0,
        )

    assert len(calls) == 1
    assert calls[0]["response_format"]["json_schema"]["strict"] is True
