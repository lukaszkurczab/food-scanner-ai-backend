from __future__ import annotations

import json

import pytest

from app.core.exceptions import OpenAIServiceError
from app.domain.chat.generator import ChatGenerator


class _FakeOpenAIClient:
    def __init__(
        self,
        *,
        chat_payload: dict | None = None,
        structured_payload: dict | None = None,
    ) -> None:
        self.chat_payload = chat_payload or {"content": "plain", "usage": {}}
        self.structured_payload = structured_payload or {
            "data": {
                "verdict": "Ok.",
                "coverageStatement": "Coverage is medium.",
                "keyObservations": ["Obs 1"],
                "practicalNextStep": "Do next.",
                "followUpQuestion": None,
            },
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
            },
        }
        self.chat_calls: list[dict] = []
        self.structured_calls: list[dict] = []

    async def chat_completion(self, **kwargs: dict) -> dict:
        self.chat_calls.append(kwargs)
        return self.chat_payload

    async def responses_json_with_usage(self, **kwargs: dict) -> dict:
        self.structured_calls.append(kwargs)
        return self.structured_payload


def _developer_contract(
    *,
    response_shape: str,
    explicit_listing_requested: bool,
    coverage_level: str | None,
    language: str = "pl",
) -> dict[str, str]:
    grounding: dict[str, object] = {}
    if coverage_level is not None:
        grounding = {
            "nutritionSummary": {
                "loggingCoverage": {
                    "coverageLevel": coverage_level,
                }
            }
        }

    payload = {
        "language": language,
        "responseShape": response_shape,
        "antiListingPolicy": {
            "explicitListingRequested": explicit_listing_requested,
        },
        "grounding": grounding,
    }
    return {"role": "developer", "content": json.dumps(payload, ensure_ascii=False)}


async def test_generator_maps_usage_from_openai_client_response_plain_mode() -> None:
    client = _FakeOpenAIClient(
        chat_payload={
            "content": "To jest odpowiedz.",
            "usage": {
                "prompt_tokens": 120,
                "completion_tokens": 30,
                "total_tokens": 150,
            },
        }
    )
    generator = ChatGenerator(client, model="gpt-4o-mini", temperature=0.1)

    result = await generator.generate(
        messages=[
            {"role": "system", "content": "x"},
            {"role": "user", "content": "y"},
        ]
    )

    assert result.text == "To jest odpowiedz."
    assert result.usage.prompt_tokens == 120
    assert result.usage.completion_tokens == 30
    assert result.usage.total_tokens == 150
    assert client.chat_calls[0]["model"] == "gpt-4o-mini"
    assert client.structured_calls == []


async def test_generator_raises_on_empty_text_plain_mode() -> None:
    client = _FakeOpenAIClient(chat_payload={"content": "", "usage": {}})
    generator = ChatGenerator(client)
    with pytest.raises(OpenAIServiceError):
        await generator.generate(messages=[{"role": "user", "content": "hej"}])


async def test_generator_enforces_verdict_first_contract_for_analytical_shape() -> None:
    client = _FakeOpenAIClient(
        structured_payload={
            "data": {
                "verdict": "Tydzien wyglada niestabilnie.",
                "coverageStatement": "Masz zapisane tylko kilka wpisow.",
                "keyObservations": [
                    "1) Wpisy sa nieregularne.",
                    "2) Bialko bywa niskie.",
                    "3) Energia jest zmienna.",
                ],
                "practicalNextStep": "Przez 3 kolejne dni zapisuj pelne posilki z gramatura.",
                "followUpQuestion": "Chcesz, zeby porownac ten tydzien do poprzedniego?",
            },
            "usage": {
                "prompt_tokens": 180,
                "completion_tokens": 45,
                "total_tokens": 225,
            },
        }
    )
    generator = ChatGenerator(client)

    result = await generator.generate(
        messages=[
            {"role": "system", "content": "x"},
            _developer_contract(
                response_shape="weekly_summary_analysis",
                explicit_listing_requested=False,
                coverage_level="low",
                language="pl",
            ),
            {"role": "user", "content": "Jak jadlem w tym tygodniu?"},
        ]
    )

    assert result.text.startswith("Werdykt:")
    assert "\n\nJakosc danych:" in result.text
    assert "\n\nNajwazniejsze obserwacje:\n- " in result.text
    assert "\n\nPraktyczny nastepny krok:" in result.text
    assert not result.text.lstrip().startswith("-")
    assert "Widze tylko czesc" in result.text
    assert result.usage.total_tokens == 225
    assert len(client.structured_calls) == 1
    assert len(client.chat_calls) == 0


async def test_generator_goal_feedback_adds_caution_for_low_coverage() -> None:
    client = _FakeOpenAIClient(
        structured_payload={
            "data": {
                "verdict": "To przybliza cie do celu redukcji.",
                "coverageStatement": "Danych jest malo.",
                "keyObservations": ["Kalorie sa nizej od celu."],
                "practicalNextStep": "Uzupelnij pelne wpisy przez kilka dni.",
                "followUpQuestion": None,
            },
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 30,
                "total_tokens": 130,
            },
        }
    )
    generator = ChatGenerator(client)

    result = await generator.generate(
        messages=[
            {"role": "system", "content": "x"},
            _developer_contract(
                response_shape="mixed_weekly_summary_and_goal",
                explicit_listing_requested=False,
                coverage_level="low",
                language="pl",
            ),
            {"role": "user", "content": "Czy to przybliza mnie do redukcji?"},
        ]
    )

    assert "To wstepna ocena, bo dane sa niepelne." in result.text


async def test_generator_keeps_listing_possible_for_explicit_listing_request() -> None:
    client = _FakeOpenAIClient(
        chat_payload={
            "content": "- 2026-04-15: owsianka\n- 2026-04-16: makaron",
            "usage": {
                "prompt_tokens": 50,
                "completion_tokens": 20,
                "total_tokens": 70,
            },
        }
    )
    generator = ChatGenerator(client)

    result = await generator.generate(
        messages=[
            {"role": "system", "content": "x"},
            _developer_contract(
                response_shape="explicit_listing_request",
                explicit_listing_requested=True,
                coverage_level="medium",
                language="pl",
            ),
            {"role": "user", "content": "Wypisz wszystkie posilki z tygodnia."},
        ]
    )

    assert result.text.startswith("-")
    assert len(client.chat_calls) == 1
    assert len(client.structured_calls) == 0
