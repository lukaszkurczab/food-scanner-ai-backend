from fastapi import FastAPI
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from app.api.routes import logs as logs_route_module
from app.api.routes.logs import router as logs_router


def create_test_client() -> TestClient:
    app = FastAPI()
    app.include_router(logs_router, prefix="/api/v1")
    return TestClient(app)


def reset_rate_limit_state() -> None:
    logs_route_module._request_buckets.clear()


def test_logs_error_endpoint_uses_authenticated_user_id(mocker: MockerFixture) -> None:
    reset_rate_limit_state()
    mocker.patch(
        "app.api.deps.auth.decode_firebase_token",
        return_value={"uid": "auth-user-1"},
    )
    log_error = mocker.patch("app.api.routes.logs.error_logger.log_error")
    client = create_test_client()

    response = client.post(
        "/api/v1/logs/error",
        json={
            "source": "mobile.scan-screen",
            "message": "Camera permission check failed",
            "stack": "stack trace",
            "context": {"platform": "ios"},
            "userId": "abc123",
        },
        headers={"Authorization": "Bearer token-123"},
    )

    assert response.status_code == 201
    assert response.json() == {"detail": "logged"}
    log_error.assert_called_once_with(
        "Camera permission check failed",
        source="mobile.scan-screen",
        stack="stack trace",
        context={"platform": "ios"},
        userId="auth-user-1",
    )


def test_logs_error_endpoint_allows_anonymous_logs(mocker: MockerFixture) -> None:
    reset_rate_limit_state()
    log_error = mocker.patch("app.api.routes.logs.error_logger.log_error")
    client = create_test_client()

    response = client.post(
        "/api/v1/logs/error",
        json={
            "source": "mobile",
            "message": "Failed to submit error log",
            "stack": None,
            "context": None,
        },
    )

    assert response.status_code == 201
    assert response.json() == {"detail": "logged"}
    log_error.assert_called_once_with(
        "Failed to submit error log",
        source="mobile",
        stack=None,
        context=None,
        userId=None,
    )


def test_logs_error_endpoint_rejects_oversized_context(mocker: MockerFixture) -> None:
    reset_rate_limit_state()
    log_error = mocker.patch("app.api.routes.logs.error_logger.log_error")
    client = create_test_client()

    response = client.post(
        "/api/v1/logs/error",
        json={
            "source": "mobile",
            "message": "payload too large",
            "context": {"blob": "x" * 9000},
        },
    )

    assert response.status_code == 422
    log_error.assert_not_called()


def test_logs_error_endpoint_rejects_non_allowlisted_context_key(
    mocker: MockerFixture,
) -> None:
    reset_rate_limit_state()
    log_error = mocker.patch("app.api.routes.logs.error_logger.log_error")
    client = create_test_client()

    response = client.post(
        "/api/v1/logs/error",
        json={
            "source": "mobile",
            "message": "invalid context key",
            "context": {"unknownKey": "value"},
        },
    )

    assert response.status_code == 422
    log_error.assert_not_called()


def test_logs_error_endpoint_rejects_privacy_sensitive_context_key(
    mocker: MockerFixture,
) -> None:
    reset_rate_limit_state()
    log_error = mocker.patch("app.api.routes.logs.error_logger.log_error")
    client = create_test_client()

    response = client.post(
        "/api/v1/logs/error",
        json={
            "source": "mobile",
            "message": "invalid context key",
            "context": {"message": "raw-user-content"},
        },
    )

    assert response.status_code == 422
    log_error.assert_not_called()


def test_logs_error_endpoint_returns_500_when_logger_fails(mocker: MockerFixture) -> None:
    reset_rate_limit_state()
    mocker.patch(
        "app.api.routes.logs.error_logger.log_error",
        side_effect=RuntimeError("logger failed"),
    )
    capture_exception = mocker.patch("app.api.routes.logs.error_logger.capture_exception")
    client = create_test_client()

    response = client.post(
        "/api/v1/logs/error",
        json={
            "source": "mobile",
            "message": "Failed to submit error log",
            "stack": None,
            "context": None,
        },
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "Failed to log error"}
    capture_exception.assert_called_once()
    assert str(capture_exception.call_args.args[0]) == "logger failed"


def test_logs_error_endpoint_returns_429_when_rate_limit_is_exceeded(
    mocker: MockerFixture,
) -> None:
    reset_rate_limit_state()
    log_error = mocker.patch("app.api.routes.logs.error_logger.log_error")
    client = create_test_client()
    mocker.patch.object(logs_route_module, "RATE_LIMIT_MAX_REQUESTS", 2)

    payload = {
        "source": "mobile",
        "message": "too many logs",
        "context": {"screen": "camera"},
    }

    assert client.post("/api/v1/logs/error", json=payload).status_code == 201
    assert client.post("/api/v1/logs/error", json=payload).status_code == 201
    response = client.post("/api/v1/logs/error", json=payload)

    assert response.status_code == 429
    assert response.json() == {"detail": "Too many log requests"}
    assert log_error.call_count == 2
