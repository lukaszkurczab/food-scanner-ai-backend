from collections.abc import Awaitable, Callable, Generator
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.api.middleware import idempotency
from app.api.middleware.idempotency import IdempotencyMiddleware


@pytest.fixture(autouse=True)
def clear_idempotency_state() -> Generator[None, None, None]:
    with idempotency._CACHE_LOCK:
        idempotency._idempotency_cache.clear()
    yield
    with idempotency._CACHE_LOCK:
        idempotency._idempotency_cache.clear()


def create_test_client(
    ai_handler: Callable[[], Awaitable[Any]],
    health_handler: Callable[[], Awaitable[Any]],
) -> TestClient:
    app = FastAPI()
    app.add_middleware(IdempotencyMiddleware)

    @app.post("/api/v2/ai/chat/runs")
    async def ai_chat_run() -> Any:
        return await ai_handler()

    @app.post("/health")
    async def health() -> Any:
        return await health_handler()

    return TestClient(app)


def test_second_request_with_same_key_returns_cached_response() -> None:
    ai_handler = AsyncMock(return_value={"detail": "ok"})
    health_handler = AsyncMock(return_value={"detail": "healthy"})
    client = create_test_client(ai_handler=ai_handler, health_handler=health_handler)
    headers = {"X-Idempotency-Key": "same-key"}

    first = client.post("/api/v2/ai/chat/runs", json={"message": "hello"}, headers=headers)
    second = client.post("/api/v2/ai/chat/runs", json={"message": "hello"}, headers=headers)

    assert first.status_code == 200
    assert first.json() == {"detail": "ok"}
    assert "X-Idempotency-Replayed" not in first.headers

    assert second.status_code == 200
    assert second.json() == {"detail": "ok"}
    assert second.headers["X-Idempotency-Replayed"] == "true"

    assert ai_handler.await_count == 1


def test_request_without_idempotency_key_passes_through_without_caching() -> None:
    ai_handler = AsyncMock(side_effect=[{"detail": "first"}, {"detail": "second"}])
    health_handler = AsyncMock(return_value={"detail": "healthy"})
    client = create_test_client(ai_handler=ai_handler, health_handler=health_handler)

    first = client.post("/api/v2/ai/chat/runs", json={"message": "hello"})
    second = client.post("/api/v2/ai/chat/runs", json={"message": "hello"})

    assert first.status_code == 200
    assert first.json() == {"detail": "first"}
    assert "X-Idempotency-Replayed" not in first.headers

    assert second.status_code == 200
    assert second.json() == {"detail": "second"}
    assert "X-Idempotency-Replayed" not in second.headers

    assert ai_handler.await_count == 2


def test_failed_first_request_is_not_cached() -> None:
    ai_handler = AsyncMock(
        side_effect=[
            JSONResponse(content={"detail": "upstream failure"}, status_code=500),
            {"detail": "ok"},
        ]
    )
    health_handler = AsyncMock(return_value={"detail": "healthy"})
    client = create_test_client(ai_handler=ai_handler, health_handler=health_handler)
    headers = {"X-Idempotency-Key": "retry-after-failure"}

    first = client.post("/api/v2/ai/chat/runs", json={"message": "hello"}, headers=headers)
    second = client.post("/api/v2/ai/chat/runs", json={"message": "hello"}, headers=headers)

    assert first.status_code == 500
    assert first.json() == {"detail": "upstream failure"}
    assert "X-Idempotency-Replayed" not in first.headers

    assert second.status_code == 200
    assert second.json() == {"detail": "ok"}
    assert "X-Idempotency-Replayed" not in second.headers

    assert ai_handler.await_count == 2


def test_non_ai_path_is_not_affected_by_idempotency_middleware() -> None:
    ai_handler = AsyncMock(return_value={"detail": "ok"})
    health_handler = AsyncMock(side_effect=[{"detail": "one"}, {"detail": "two"}])
    client = create_test_client(ai_handler=ai_handler, health_handler=health_handler)
    headers = {"X-Idempotency-Key": "non-ai-path"}

    first = client.post("/health", headers=headers)
    second = client.post("/health", headers=headers)

    assert first.status_code == 200
    assert first.json() == {"detail": "one"}
    assert "X-Idempotency-Replayed" not in first.headers

    assert second.status_code == 200
    assert second.json() == {"detail": "two"}
    assert "X-Idempotency-Replayed" not in second.headers

    assert health_handler.await_count == 2
