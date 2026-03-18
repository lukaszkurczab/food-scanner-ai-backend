from hashlib import sha256

from pytest_mock import MockerFixture

from app.services.ai_gateway_logger import COLLECTION_NAME, log_gateway_decision


def test_log_gateway_decision_persists_gateway_entry(mocker: MockerFixture) -> None:
    client = mocker.Mock()
    collection_ref = mocker.Mock()
    client.collection.return_value = collection_ref
    mocker.patch("app.services.ai_gateway_logger.get_firestore", return_value=client)

    log_gateway_decision(
        "user-1",
        "Ile kalorii ma jablko?",
        {
            "decision": "FORWARD",
            "reason": "OK",
            "score": 1.0,
            "credit_cost": 1.0,
            "request_id": "req-1",
            "action_type": "chat",
            "task_type": "chat",
            "hypothetical_decision": "LOCAL_ANSWER",
            "hypothetical_reason": "TRIVIAL_GREETING",
            "enforced": False,
            "model": "gpt-4o-mini",
            "estimated_tokens": 12,
            "actual_tokens": None,
            "latency_ms": 111.11,
            "estimated_cost": 1.0,
        },
        "chat",
        language="pl",
        response_time_ms=123.456,
        execution_time_ms=234.567,
        profile="free",
        tier="free",
        credit_cost=1.0,
    )

    client.collection.assert_called_once_with(COLLECTION_NAME)
    collection_ref.add.assert_called_once()
    payload = collection_ref.add.call_args.args[0]
    assert payload["userId"] == "user-1"
    assert payload["actionType"] == "chat"
    assert payload["messageHash"] == sha256("Ile kalorii ma jablko?".encode("utf-8")).hexdigest()
    assert payload["decision"] == "FORWARD"
    assert payload["reason"] == "OK"
    assert payload["score"] == 1.0
    assert payload["creditCost"] == 1.0
    assert payload["language"] == "pl"
    assert payload["length"] == len("Ile kalorii ma jablko?")
    assert payload["responseTimeMs"] == 123.46
    assert payload["executionTimeMs"] == 234.57
    assert payload["profile"] == "free"
    assert payload["tier"] == "free"
    assert payload["requestId"] == "req-1"
    assert payload["taskType"] == "chat"
    assert payload["hypotheticalDecision"] == "LOCAL_ANSWER"
    assert payload["hypotheticalReason"] == "TRIVIAL_GREETING"
    assert payload["enforced"] is False
    assert payload["model"] == "gpt-4o-mini"
    assert payload["estimatedTokens"] == 12
    assert payload["actualTokens"] is None
    assert payload["latencyMs"] == 111.11
    assert payload["estimatedCost"] == 1.0
