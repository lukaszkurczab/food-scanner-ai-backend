"""Integration tests for the AI ask endpoint with mocked dependencies and Firestore logs."""

from unittest.mock import MagicMock

from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from app.core.config import settings
from app.core.exceptions import (
    AiUsageLimitExceededError,
    ContentBlockedError,
    FirestoreServiceError,
    OpenAIServiceError,
)
from app.main import app

client = TestClient(app)


def _mock_gateway_firestore(mocker: MockerFixture) -> tuple[MagicMock, MagicMock]:
    firestore_client = mocker.Mock()
    collection_ref = mocker.Mock()
    firestore_client.collection.return_value = collection_ref
    mocker.patch(
        "app.services.ai_gateway_logger.get_firestore",
        return_value=firestore_client,
    )
    return firestore_client, collection_ref


def test_post_ai_ask_returns_reply_and_logs_gateway_document(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    check_allowed = mocker.patch("app.api.routes.ai.content_guard_service.check_allowed")
    evaluate_request = mocker.patch(
        "app.api.routes.ai.ai_gateway_service.evaluate_request",
        return_value={
            "decision": "FORWARD",
            "reason": "OK",
            "score": 1.0,
            "credit_cost": 1.0,
        },
    )
    firestore_client, collection_ref = _mock_gateway_firestore(mocker)
    sanitize_context = mocker.patch(
        "app.api.routes.ai.sanitization_service.sanitize_context",
        return_value={"weightKg": "70-80"},
    )
    sanitize_request = mocker.patch(
        "app.api.routes.ai.sanitization_service.sanitize_request",
        return_value="sanitized prompt",
    )
    build_chat_prompt = mocker.patch(
        "app.api.routes.ai.ai_chat_prompt_service.build_chat_prompt",
        return_value="chat prompt",
    )
    increment_usage = mocker.patch(
        "app.api.routes.ai.ai_usage_service.increment_usage",
        return_value=(4.0, 20, "2026-03-02", 16.0),
    )
    ask_chat = mocker.patch(
        "app.api.routes.ai.openai_service.ask_chat",
        return_value="Try grilled chicken with rice.",
    )

    response = client.post(
        "/api/v1/ai/ask",
        json={
            "message": "Suggest a dinner",
            "context": {"weightKg": 78},
        },
        headers=auth_headers("abc"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "reply": "Try grilled chicken with rice.",
        "usageCount": 4.0,
        "remaining": 16.0,
        "dateKey": "2026-03-02",
        "version": settings.VERSION,
        "persistence": "backend_owned",
    }
    check_allowed.assert_called_once_with("Suggest a dinner")
    evaluate_request.assert_called_once_with("abc", "chat", "Suggest a dinner", language="pl")
    sanitize_context.assert_called_once_with({"weightKg": 78})
    sanitize_request.assert_called_once_with("Suggest a dinner", {"weightKg": "70-80"})
    build_chat_prompt.assert_called_once_with(
        "sanitized prompt",
        {"weightKg": "70-80"},
        language="pl",
    )
    increment_usage.assert_called_once_with("abc", cost=1.0)
    ask_chat.assert_called_once_with("chat prompt")
    firestore_client.collection.assert_called_once_with("ai_gateway_logs")
    collection_ref.add.assert_called_once()
    logged_doc = collection_ref.add.call_args.args[0]
    assert logged_doc["userId"] == "abc"
    assert logged_doc["decision"] == "FORWARD"
    assert logged_doc["reason"] == "OK"
    assert logged_doc["creditCost"] == 1.0
    assert logged_doc["length"] == len("Suggest a dinner")
    assert logged_doc["actionType"] == "chat"


def test_post_ai_ask_requires_required_fields(auth_headers) -> None:
    response = client.post(
        "/api/v1/ai/ask",
        json={},
        headers=auth_headers("abc"),
    )

    assert response.status_code == 422


def test_post_ai_ask_returns_403_when_content_is_blocked(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch(
        "app.api.routes.ai.content_guard_service.check_allowed",
        side_effect=ContentBlockedError("blocked"),
    )

    response = client.post(
        "/api/v1/ai/ask",
        json={"message": "therapy advice"},
        headers=auth_headers("abc"),
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "blocked"}


def test_post_ai_ask_returns_429_when_limit_is_exceeded(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch("app.api.routes.ai.content_guard_service.check_allowed")
    mocker.patch(
        "app.api.routes.ai.ai_gateway_service.evaluate_request",
        return_value={
            "decision": "FORWARD",
            "reason": "OK",
            "score": 1.0,
            "credit_cost": 1.0,
        },
    )
    mocker.patch(
        "app.api.routes.ai.sanitization_service.sanitize_context",
        return_value=None,
    )
    mocker.patch(
        "app.api.routes.ai.sanitization_service.sanitize_request",
        return_value="sanitized prompt",
    )
    mocker.patch(
        "app.api.routes.ai.ai_usage_service.increment_usage",
        side_effect=AiUsageLimitExceededError("limit"),
    )

    response = client.post(
        "/api/v1/ai/ask",
        json={"message": "Suggest a dinner"},
        headers=auth_headers("abc"),
    )

    assert response.status_code == 429
    assert response.json() == {"detail": "AI usage limit exceeded"}


def test_post_ai_ask_returns_500_when_firestore_fails(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch("app.api.routes.ai.content_guard_service.check_allowed")
    mocker.patch(
        "app.api.routes.ai.ai_gateway_service.evaluate_request",
        return_value={
            "decision": "FORWARD",
            "reason": "OK",
            "score": 1.0,
            "credit_cost": 1.0,
        },
    )
    mocker.patch(
        "app.api.routes.ai.sanitization_service.sanitize_context",
        return_value=None,
    )
    mocker.patch(
        "app.api.routes.ai.sanitization_service.sanitize_request",
        return_value="sanitized prompt",
    )
    mocker.patch(
        "app.api.routes.ai.ai_usage_service.increment_usage",
        side_effect=FirestoreServiceError("db down"),
    )

    response = client.post(
        "/api/v1/ai/ask",
        json={"message": "Suggest a dinner"},
        headers=auth_headers("abc"),
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "Database error"}


def test_post_ai_ask_returns_503_when_openai_fails(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch("app.api.routes.ai.content_guard_service.check_allowed")
    mocker.patch(
        "app.api.routes.ai.ai_gateway_service.evaluate_request",
        return_value={
            "decision": "FORWARD",
            "reason": "OK",
            "score": 1.0,
            "credit_cost": 1.0,
        },
    )
    _mock_gateway_firestore(mocker)
    mocker.patch(
        "app.api.routes.ai.sanitization_service.sanitize_context",
        return_value=None,
    )
    mocker.patch(
        "app.api.routes.ai.sanitization_service.sanitize_request",
        return_value="sanitized prompt",
    )
    mocker.patch(
        "app.api.routes.ai.ai_chat_prompt_service.build_chat_prompt",
        return_value="chat prompt",
    )
    mocker.patch(
        "app.api.routes.ai.ai_usage_service.increment_usage",
        return_value=(1.0, 20, "2026-03-02", 19.0),
    )
    mocker.patch(
        "app.api.routes.ai.openai_service.ask_chat",
        side_effect=OpenAIServiceError("unavailable"),
    )

    response = client.post(
        "/api/v1/ai/ask",
        json={"message": "Suggest a dinner"},
        headers=auth_headers("abc"),
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "AI service unavailable"}


def test_post_ai_ask_rejects_off_topic_request_with_partial_cost_and_logs_document(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch("app.api.routes.ai.content_guard_service.check_allowed")
    mocker.patch(
        "app.api.routes.ai.ai_gateway_service.evaluate_request",
        return_value={
            "decision": "REJECT",
            "reason": "OFF_TOPIC",
            "score": -0.8,
            "credit_cost": 0.2,
        },
    )
    mocker.patch(
        "app.api.routes.ai.sanitization_service.sanitize_context",
        return_value=None,
    )
    mocker.patch(
        "app.api.routes.ai.sanitization_service.sanitize_request",
        return_value="sanitized prompt",
    )
    increment_usage = mocker.patch(
        "app.api.routes.ai.ai_usage_service.increment_usage",
        return_value=(0.2, 20, "2026-03-02", 19.8),
    )
    _firestore_client, collection_ref = _mock_gateway_firestore(mocker)
    ask_chat = mocker.patch("app.api.routes.ai.openai_service.ask_chat")

    response = client.post(
        "/api/v1/ai/ask",
        json={"message": "Jaka bedzie pogoda jutro?"},
        headers=auth_headers("abc"),
    )

    assert response.status_code == 400
    assert response.json() == {"reason": "OFF_TOPIC", "credit_cost": 0.2}
    increment_usage.assert_called_once_with("abc", cost=0.2)
    ask_chat.assert_not_called()
    collection_ref.add.assert_called_once()
    logged_doc = collection_ref.add.call_args.args[0]
    assert logged_doc["userId"] == "abc"
    assert logged_doc["decision"] == "REJECT"
    assert logged_doc["reason"] == "OFF_TOPIC"
    assert logged_doc["creditCost"] == 0.2


def test_post_ai_ask_returns_local_answer_when_gateway_handles_request(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch("app.api.routes.ai.content_guard_service.check_allowed")
    mocker.patch(
        "app.api.routes.ai.ai_gateway_service.evaluate_request",
        return_value={
            "decision": "LOCAL_ANSWER",
            "reason": "LOCAL_PRODUCT_MATCH",
            "score": 0.9,
            "credit_cost": 0.5,
        },
    )
    mocker.patch(
        "app.api.routes.ai.sanitization_service.sanitize_context",
        return_value=None,
    )
    mocker.patch(
        "app.api.routes.ai.sanitization_service.sanitize_request",
        return_value="sanitized prompt",
    )
    increment_usage = mocker.patch(
        "app.api.routes.ai.ai_usage_service.increment_usage",
        return_value=(0.5, 20, "2026-03-02", 19.5),
    )
    _mock_gateway_firestore(mocker)
    ask_chat = mocker.patch("app.api.routes.ai.openai_service.ask_chat")

    response = client.post(
        "/api/v1/ai/ask",
        json={"message": "Ile kalorii ma jablko?"},
        headers=auth_headers("abc"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "reply": "To zapytanie zostalo obsluzone lokalnie.",
        "usageCount": 0.5,
        "remaining": 19.5,
        "dateKey": "2026-03-02",
        "version": settings.VERSION,
        "persistence": "backend_owned",
    }
    increment_usage.assert_called_once_with("abc", cost=0.5)
    ask_chat.assert_not_called()


def test_post_ai_ask_skips_gateway_evaluation_when_feature_flag_is_disabled(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch("app.api.routes.ai.content_guard_service.check_allowed")
    evaluate_request = mocker.patch("app.api.routes.ai.ai_gateway_service.evaluate_request")
    _mock_gateway_firestore(mocker)
    mocker.patch(
        "app.api.routes.ai.sanitization_service.sanitize_context",
        return_value=None,
    )
    mocker.patch(
        "app.api.routes.ai.sanitization_service.sanitize_request",
        return_value="sanitized prompt",
    )
    mocker.patch(
        "app.api.routes.ai.ai_chat_prompt_service.build_chat_prompt",
        return_value="chat prompt",
    )
    mocker.patch("app.api.routes.ai.settings.AI_GATEWAY_ENABLED", False)
    increment_usage = mocker.patch(
        "app.api.routes.ai.ai_usage_service.increment_usage",
        return_value=(1.0, 20, "2026-03-02", 19.0),
    )
    ask_chat = mocker.patch(
        "app.api.routes.ai.openai_service.ask_chat",
        return_value="Try grilled chicken with rice.",
    )

    response = client.post(
        "/api/v1/ai/ask",
        json={"message": "Suggest a dinner"},
        headers=auth_headers("abc"),
    )

    assert response.status_code == 200
    evaluate_request.assert_not_called()
    increment_usage.assert_called_once_with("abc", cost=1.0)
    ask_chat.assert_called_once_with("chat prompt")


def test_post_ai_ask_bypasses_gateway_for_non_chat_action_type(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch("app.api.routes.ai.content_guard_service.check_allowed")
    evaluate_request = mocker.patch("app.api.routes.ai.ai_gateway_service.evaluate_request")
    _firestore_client, collection_ref = _mock_gateway_firestore(mocker)
    mocker.patch(
        "app.api.routes.ai.sanitization_service.sanitize_context",
        return_value={"actionType": "meal_text_analysis", "lang": "en"},
    )
    mocker.patch(
        "app.api.routes.ai.sanitization_service.sanitize_request",
        return_value="sanitized prompt",
    )
    increment_usage = mocker.patch(
        "app.api.routes.ai.ai_usage_service.increment_usage",
        return_value=(1.0, 20, "2026-03-02", 19.0),
    )
    ask_chat = mocker.patch(
        "app.api.routes.ai.openai_service.ask_chat",
        return_value='[{"name":"Rice","amount":100,"protein":2,"fat":0,"carbs":28,"kcal":130}]',
    )

    response = client.post(
        "/api/v1/ai/ask",
        json={
            "message": "Analyze this meal payload",
            "context": {
                "actionType": "meal_text_analysis",
                "lang": "en",
            },
        },
        headers=auth_headers("abc"),
    )

    assert response.status_code == 200
    assert response.json()["reply"].startswith("[{\"name\":\"Rice\"")
    evaluate_request.assert_not_called()
    increment_usage.assert_called_once_with("abc", cost=1.0)
    ask_chat.assert_called_once_with("sanitized prompt")
    collection_ref.add.assert_called_once()
    logged_doc = collection_ref.add.call_args.args[0]
    assert logged_doc["actionType"] == "meal_text_analysis"


def test_post_ai_ask_uses_uid_from_token(
    auth_headers,
    mocker: MockerFixture,
) -> None:
    mocker.patch("app.api.routes.ai.content_guard_service.check_allowed")
    mocker.patch("app.api.routes.ai.ai_gateway_logger.log_gateway_decision")
    mocker.patch(
        "app.api.routes.ai.ai_gateway_service.evaluate_request",
        return_value={
            "decision": "LOCAL_ANSWER",
            "reason": "LOCAL_PRODUCT_MATCH",
            "score": 0.9,
            "credit_cost": 0.5,
        },
    )
    increment_usage = mocker.patch(
        "app.api.routes.ai.ai_usage_service.increment_usage",
        return_value=(0.5, 20, "2026-03-02", 19.5),
    )
    mocker.patch(
        "app.api.routes.ai.sanitization_service.sanitize_context",
        return_value=None,
    )
    mocker.patch(
        "app.api.routes.ai.sanitization_service.sanitize_request",
        return_value="prompt",
    )

    response = client.post(
        "/api/v1/ai/ask",
        json={"message": "Suggest a dinner"},
        headers=auth_headers("other-user"),
    )

    assert response.status_code == 200
    increment_usage.assert_called_once_with("other-user", cost=0.5)
