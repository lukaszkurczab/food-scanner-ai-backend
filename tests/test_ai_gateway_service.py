import pytest

from app.core.config import settings
from app.services.ai_gateway_service import (
    FORWARD_REASON_GATEWAY_DISABLED,
    FORWARD_REASON_PASS_THROUGH,
    HYPOTHESIS_TRIVIAL_GREETING,
    REJECT_REASON_OFF_TOPIC,
    REJECT_REASON_TOO_SHORT,
    classify_task_type,
    evaluate_request,
)


# ---------------------------------------------------------------------------
# Mobile contract — canonical reject reasons must stay in sync
# ---------------------------------------------------------------------------

# Mirror of mobile's GATEWAY_REJECT_REASONS (useChatHistory.ts).
# If this set changes, update the mobile constant too.
MOBILE_GATEWAY_REJECT_REASONS = {"OFF_TOPIC", "ML_OFF_TOPIC", "TOO_SHORT"}


def test_canonical_reject_reasons_match_mobile_contract() -> None:
    """Backend reject reason constants must be members of the mobile set."""
    assert REJECT_REASON_OFF_TOPIC in MOBILE_GATEWAY_REJECT_REASONS
    assert REJECT_REASON_TOO_SHORT in MOBILE_GATEWAY_REJECT_REASONS


# ---------------------------------------------------------------------------
# Off-topic enforcement (the one real REJECT rule)
# ---------------------------------------------------------------------------


def test_evaluate_request_rejects_off_topic_chat_when_gateway_enabled() -> None:
    result = evaluate_request("user-1", "chat", "Jaka bedzie pogoda jutro?")

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
def test_evaluate_request_rejects_all_off_topic_keywords(message: str) -> None:
    """Every off-topic keyword triggers REJECT with canonical OFF_TOPIC reason."""
    result = evaluate_request("user-1", "chat", message)

    assert result["decision"] == "REJECT"
    assert result["reason"] == REJECT_REASON_OFF_TOPIC
    assert result["enforced"] is True


def test_off_topic_message_on_non_chat_route_is_forwarded() -> None:
    """Off-topic enforcement only applies to chat action type."""
    result = evaluate_request("user-1", "photo_analysis", "pogoda bitcoin lotto")

    assert result["decision"] == "FORWARD"
    assert result["reason"] == FORWARD_REASON_PASS_THROUGH
    # Still records the hypothetical for observability
    assert result.get("hypothetical_decision") == "REJECT"
    assert result.get("hypothetical_reason") == REJECT_REASON_OFF_TOPIC


# ---------------------------------------------------------------------------
# Diet-related / normal chat — must FORWARD
# ---------------------------------------------------------------------------


def test_evaluate_request_forwards_diet_related_chat() -> None:
    result = evaluate_request("user-1", "chat", "Ile bialka ma kurczak z ryzem?")

    assert result["decision"] == "FORWARD"
    assert result["reason"] == FORWARD_REASON_PASS_THROUGH
    assert result["task_type"] == "chat"
    assert result["enforced"] is False
    assert "hypothetical_decision" not in result


# ---------------------------------------------------------------------------
# Disabled gateway — always FORWARD
# ---------------------------------------------------------------------------


def test_evaluate_request_forwards_when_gateway_disabled(mocker) -> None:
    mocker.patch("app.services.ai_gateway_service.settings.AI_GATEWAY_ENABLED", False)

    result = evaluate_request("user-1", "chat", "hej")

    assert result["decision"] == "FORWARD"
    assert result["reason"] == FORWARD_REASON_GATEWAY_DISABLED
    assert result["task_type"] == "chat"
    assert result["estimated_cost"] == 1.0
    assert "hypothetical_decision" not in result


def test_gateway_disabled_does_not_reject_off_topic(mocker) -> None:
    """When gateway is disabled, even off-topic messages are forwarded."""
    mocker.patch("app.services.ai_gateway_service.settings.AI_GATEWAY_ENABLED", False)

    result = evaluate_request("user-1", "chat", "Jaka pogoda jutro?")

    assert result["decision"] == "FORWARD"
    assert result["reason"] == FORWARD_REASON_GATEWAY_DISABLED


# ---------------------------------------------------------------------------
# Hypothetical classification (logged, not enforced)
# ---------------------------------------------------------------------------


def test_evaluate_request_marks_trivial_chat_as_local_answer_hypothesis() -> None:
    result = evaluate_request("user-1", "chat", "hej")

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


def test_reject_result_contains_fields_needed_by_route() -> None:
    """The route reads decision, reason, score from the gateway result to
    build the HTTP 400 detail.  Verify these fields are present and typed."""
    result = evaluate_request("user-1", "chat", "horoscope today")

    assert result["decision"] == "REJECT"
    assert isinstance(result["reason"], str)
    assert isinstance(result["score"], float)
    # Route also reads request_id for logging
    assert isinstance(result["request_id"], str)
