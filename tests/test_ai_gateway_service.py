from app.services.ai_gateway_service import classify_task_type, evaluate_request


def test_evaluate_request_forwards_when_gateway_enabled() -> None:
    result = evaluate_request("user-1", "chat", "Jaka bedzie pogoda jutro?")

    assert result["decision"] == "FORWARD"
    assert result["reason"] == "PASS_THROUGH"
    assert result["task_type"] == "chat"
    assert result["model"] == "gpt-4o-mini"
    assert result["estimated_tokens"] > 0
    assert result["estimated_cost"] == 1.0
    assert result.get("hypothetical_decision") == "REJECT"
    assert result.get("hypothetical_reason") == "LIKELY_OFF_TOPIC"
    assert result["enforced"] is False
    assert isinstance(result["request_id"], str)


def test_evaluate_request_forwards_when_gateway_disabled(mocker) -> None:
    mocker.patch("app.services.ai_gateway_service.settings.AI_GATEWAY_ENABLED", False)

    result = evaluate_request("user-1", "chat", "hej")

    assert result["decision"] == "FORWARD"
    assert result["reason"] == "GATEWAY_DISABLED"
    assert result["task_type"] == "chat"
    assert result["estimated_cost"] == 1.0
    assert "hypothetical_decision" not in result


def test_classify_task_type_supports_supported_categories() -> None:
    assert classify_task_type("chat") == "chat"
    assert classify_task_type("photo_analysis") == "photo_meal_analysis"
    assert classify_task_type("text_meal_analysis") == "text_meal_analysis"
    assert classify_task_type("other_action") == "other"


def test_evaluate_request_marks_trivial_chat_as_local_answer_hypothesis() -> None:
    result = evaluate_request("user-1", "chat", "hej")

    assert result["decision"] == "FORWARD"
    assert result.get("hypothetical_decision") == "LOCAL_ANSWER"
    assert result.get("hypothetical_reason") == "TRIVIAL_GREETING"
