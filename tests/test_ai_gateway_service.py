from collections import deque
from time import monotonic

import pytest
from pytest_mock import MockerFixture

import app.services.ai_gateway_service as _gw
from app.core.config import settings
from app.services.ai_gateway_service import (
    FORWARD_REASON_GATEWAY_DISABLED,
    FORWARD_REASON_PASS_THROUGH,
    GUARD_REASON_MESSAGE_TOO_LONG,
    GUARD_REASON_PAYLOAD_TOO_LARGE,
    GUARD_REASON_RATE_LIMITED,
    HYPOTHESIS_TRIVIAL_GREETING,
    RATE_LIMIT_MAX_REQUESTS,
    RATE_LIMIT_WINDOW_SECONDS,
    REJECT_REASON_OFF_TOPIC,
    REJECT_REASON_TOO_SHORT,
    classify_task_type,
    evaluate_request,
    reset_rate_limit_state,
)


# ---------------------------------------------------------------------------
# Mobile contract — canonical reject reasons must stay in sync
# ---------------------------------------------------------------------------

# Mirror of mobile's GATEWAY_REJECT_REASONS (useChatHistory.ts).
# If this set changes, update the mobile constant too.
MOBILE_GATEWAY_REJECT_REASONS = {"OFF_TOPIC", "ML_OFF_TOPIC", "TOO_SHORT"}


@pytest.fixture(autouse=True)
def _mock_rate_limit(mocker: MockerFixture) -> None:
    """Replace the Firestore-backed rate limiter with a fast in-memory one.

    Tests control the effective limit via ``RATE_LIMIT_MAX_REQUESTS``.  Each
    test gets a fresh bucket so concurrent test runs cannot interfere.
    """
    buckets: dict[str, deque[float]] = {}

    async def _in_memory_slot(user_id: str) -> bool:
        # Read the module attributes at call time so mocker.patch works.
        now = monotonic()
        bucket = buckets.setdefault(user_id, deque())
        while bucket and now - bucket[0] >= _gw.RATE_LIMIT_WINDOW_SECONDS:
            bucket.popleft()
        if len(bucket) >= _gw.RATE_LIMIT_MAX_REQUESTS:
            return False
        bucket.append(now)
        return True

    mocker.patch(
        "app.services.ai_gateway_service._consume_rate_limit_slot",
        side_effect=_in_memory_slot,
    )


def test_canonical_reject_reasons_match_mobile_contract() -> None:
    """Backend reject reason constants must be members of the mobile set."""
    assert REJECT_REASON_OFF_TOPIC in MOBILE_GATEWAY_REJECT_REASONS
    assert REJECT_REASON_TOO_SHORT in MOBILE_GATEWAY_REJECT_REASONS


# ---------------------------------------------------------------------------
# Off-topic enforcement (the one real REJECT rule)
# ---------------------------------------------------------------------------


async def test_evaluate_request_rejects_off_topic_chat_when_gateway_enabled() -> None:
    result = await evaluate_request("user-1", "chat", "Jaka bedzie pogoda jutro?")

    assert result["decision"] == "REJECT"
    assert result["reason"] == REJECT_REASON_OFF_TOPIC
    assert result["task_type"] == "chat"
    assert result["model"] == "gpt-4o-mini"
    assert result["estimated_tokens"] > 0
    assert result["estimated_cost"] == settings.AI_REJECT_COST
    assert result.get("hypothetical_decision") == "REJECT"
    assert result.get("hypothetical_reason") == REJECT_REASON_OFF_TOPIC
    assert result["enforced"] is True
    assert isinstance(result["request_id"], str)


@pytest.mark.parametrize(
    "message",
    [
        "Jaka jest pogoda?",
        "What's the weather like?",
        "Ile kosztuje bitcoin?",
        "Jaki jest wynik meczu?",
        "what is the match score?",
        "lotto numbers today",
        "daily horoscope",
        "mój horoskop na dziś",
    ],
)
async def test_evaluate_request_rejects_all_off_topic_keywords(message: str) -> None:
    """Every off-topic keyword triggers REJECT with canonical OFF_TOPIC reason."""
    result = await evaluate_request("user-1", "chat", message)

    assert result["decision"] == "REJECT"
    assert result["reason"] == REJECT_REASON_OFF_TOPIC
    assert result["enforced"] is True


async def test_off_topic_message_on_non_chat_route_is_forwarded() -> None:
    """Off-topic enforcement only applies to chat action type."""
    result = await evaluate_request("user-1", "photo_analysis", "pogoda bitcoin lotto")

    assert result["decision"] == "FORWARD"
    assert result["reason"] == FORWARD_REASON_PASS_THROUGH
    # Still records the hypothetical for observability
    assert result.get("hypothetical_decision") == "REJECT"
    assert result.get("hypothetical_reason") == REJECT_REASON_OFF_TOPIC


# ---------------------------------------------------------------------------
# Diet-related / normal chat — must FORWARD
# ---------------------------------------------------------------------------


async def test_evaluate_request_forwards_diet_related_chat() -> None:
    result = await evaluate_request("user-1", "chat", "Ile bialka ma kurczak z ryzem?")

    assert result["decision"] == "FORWARD"
    assert result["reason"] == FORWARD_REASON_PASS_THROUGH
    assert result["task_type"] == "chat"
    assert result["enforced"] is False
    assert "hypothetical_decision" not in result


# ---------------------------------------------------------------------------
# Disabled gateway — always FORWARD
# ---------------------------------------------------------------------------


async def test_evaluate_request_forwards_when_gateway_disabled(mocker: MockerFixture) -> None:
    mocker.patch("app.services.ai_gateway_service.settings.AI_GATEWAY_ENABLED", False)

    result = await evaluate_request("user-1", "chat", "hej")

    assert result["decision"] == "FORWARD"
    assert result["reason"] == FORWARD_REASON_GATEWAY_DISABLED
    assert result["task_type"] == "chat"
    assert result["estimated_cost"] == 1.0
    assert "hypothetical_decision" not in result


async def test_gateway_disabled_does_not_reject_off_topic(mocker: MockerFixture) -> None:
    """When gateway is disabled, even off-topic messages are forwarded."""
    mocker.patch("app.services.ai_gateway_service.settings.AI_GATEWAY_ENABLED", False)

    result = await evaluate_request("user-1", "chat", "Jaka pogoda jutro?")

    assert result["decision"] == "FORWARD"
    assert result["reason"] == FORWARD_REASON_GATEWAY_DISABLED


async def test_gateway_disabled_bypasses_rate_limit_and_payload_guards(mocker: MockerFixture) -> None:
    mocker.patch("app.services.ai_gateway_service.settings.AI_GATEWAY_ENABLED", False)
    mocker.patch("app.services.ai_gateway_service.RATE_LIMIT_MAX_REQUESTS", 0)
    mocker.patch("app.services.ai_gateway_service.MAX_CHAT_MESSAGE_CHARS", 1)

    result = await evaluate_request("user-1", "chat", "still forwarded")

    assert result["decision"] == "FORWARD"
    assert result["reason"] == FORWARD_REASON_GATEWAY_DISABLED
    assert result["enforced"] is False


async def test_evaluate_request_rate_limits_per_user(mocker: MockerFixture) -> None:
    mocker.patch("app.services.ai_gateway_service.RATE_LIMIT_MAX_REQUESTS", 1)

    first = await evaluate_request("user-1", "chat", "Ile bialka ma jajko?")
    second = await evaluate_request("user-1", "chat", "Ile bialka ma twarog?")

    assert first["decision"] == "FORWARD"
    assert second["decision"] == "REJECT"
    assert second["reason"] == GUARD_REASON_RATE_LIMITED
    assert second["enforced"] is True


async def test_evaluate_request_rejects_chat_message_that_is_too_long(mocker: MockerFixture) -> None:
    mocker.patch("app.services.ai_gateway_service.MAX_CHAT_MESSAGE_CHARS", 10)

    result = await evaluate_request("user-1", "chat", "To jest zdecydowanie za dluga wiadomosc")

    assert result["decision"] == "REJECT"
    assert result["reason"] == GUARD_REASON_MESSAGE_TOO_LONG
    assert result["enforced"] is True


async def test_evaluate_request_rejects_payload_that_is_too_large(mocker: MockerFixture) -> None:
    mocker.patch("app.services.ai_gateway_service.MAX_TEXT_PAYLOAD_CHARS", 10)

    result = await evaluate_request(
        "user-1",
        "text_meal_analysis",
        '{"name":"owsianka"}',
        raw_payload_chars=99,
    )

    assert result["decision"] == "REJECT"
    assert result["reason"] == GUARD_REASON_PAYLOAD_TOO_LARGE
    assert result["enforced"] is True


# ---------------------------------------------------------------------------
# Hypothetical classification (logged, not enforced)
# ---------------------------------------------------------------------------


async def test_evaluate_request_marks_trivial_chat_as_local_answer_hypothesis() -> None:
    result = await evaluate_request("user-1", "chat", "hej")

    assert result["decision"] == "FORWARD"
    assert result.get("hypothetical_decision") == "LOCAL_ANSWER"
    assert result.get("hypothetical_reason") == HYPOTHESIS_TRIVIAL_GREETING


# ---------------------------------------------------------------------------
# Task type classification
# ---------------------------------------------------------------------------


def test_classify_task_type_supports_supported_categories() -> None:
    assert classify_task_type("chat") == "chat"
    assert classify_task_type("photo_analysis") == "photo_meal_analysis"
    assert classify_task_type("text_meal_analysis") == "text_meal_analysis"
    assert classify_task_type("other_action") == "other"


# ---------------------------------------------------------------------------
# Route reject handling — gateway result flows to HTTP 400
# ---------------------------------------------------------------------------


async def test_reject_result_contains_fields_needed_by_route() -> None:
    """The route reads decision, reason, score from the gateway result to
    build the HTTP 400 detail.  Verify these fields are present and typed."""
    result = await evaluate_request("user-1", "chat", "horoscope today")

    assert result["decision"] == "REJECT"
    assert isinstance(result["reason"], str)
    assert isinstance(result["score"], float)
    # Route also reads request_id for logging
    assert isinstance(result["request_id"], str)


# ---------------------------------------------------------------------------
# reset_rate_limit_state — backward-compat no-op
# ---------------------------------------------------------------------------


def test_reset_rate_limit_state_is_safe_to_call() -> None:
    reset_rate_limit_state()  # must not raise
