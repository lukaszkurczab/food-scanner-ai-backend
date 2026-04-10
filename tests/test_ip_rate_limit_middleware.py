from collections.abc import Generator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from app.api.middleware.ip_rate_limit import IpRateLimitMiddleware
from app.api.middleware import ip_rate_limit


@pytest.fixture(autouse=True)
def clear_rate_limit_state() -> Generator[None, None, None]:
    with ip_rate_limit._BUCKET_LOCK:
        ip_rate_limit._ip_buckets.clear()
    yield
    with ip_rate_limit._BUCKET_LOCK:
        ip_rate_limit._ip_buckets.clear()


def create_test_client() -> TestClient:
    app = FastAPI()
    app.add_middleware(IpRateLimitMiddleware)

    @app.get("/ok")
    async def ok() -> dict[str, str]:
        return {"detail": "ok"}

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"detail": "healthy"}

    @app.get("/health/firestore")
    async def health_firestore() -> dict[str, str]:
        return {"detail": "healthy"}

    return TestClient(app)


def test_normal_request_passes_through() -> None:
    client = create_test_client()

    response = client.get("/ok")

    assert response.status_code == 200
    assert response.json() == {"detail": "ok"}


def test_exceeding_limit_returns_429_with_retry_after_header(
    mocker: MockerFixture,
) -> None:
    mocker.patch("app.api.middleware.ip_rate_limit._RATE_LIMIT_MAX_REQUESTS", 2)
    client = create_test_client()
    headers = {"X-Forwarded-For": "198.51.100.10"}

    assert client.get("/ok", headers=headers).status_code == 200
    assert client.get("/ok", headers=headers).status_code == 200
    response = client.get("/ok", headers=headers)

    assert response.status_code == 429
    assert response.json() == {"detail": "Too many requests"}
    assert response.headers["Retry-After"] == "60"


def test_health_paths_are_exempt_even_when_limit_is_exceeded(
    mocker: MockerFixture,
) -> None:
    mocker.patch("app.api.middleware.ip_rate_limit._RATE_LIMIT_MAX_REQUESTS", 1)
    client = create_test_client()
    headers = {"X-Forwarded-For": "203.0.113.5"}

    assert client.get("/ok", headers=headers).status_code == 200
    assert client.get("/ok", headers=headers).status_code == 429
    assert client.get("/health", headers=headers).status_code == 200
    assert client.get("/health/firestore", headers=headers).status_code == 200


def test_x_forwarded_for_header_is_used_when_present(
    mocker: MockerFixture,
) -> None:
    mocker.patch("app.api.middleware.ip_rate_limit._RATE_LIMIT_MAX_REQUESTS", 1)
    client = create_test_client()
    forwarded_headers = {"X-Forwarded-For": "198.51.100.3, 10.0.0.2"}

    assert client.get("/ok", headers=forwarded_headers).status_code == 200
    assert client.get("/ok").status_code == 200
    assert client.get("/ok", headers=forwarded_headers).status_code == 429


def test_different_ips_have_independent_buckets(
    mocker: MockerFixture,
) -> None:
    mocker.patch("app.api.middleware.ip_rate_limit._RATE_LIMIT_MAX_REQUESTS", 1)
    client = create_test_client()
    ip_one_headers = {"X-Forwarded-For": "198.51.100.11"}
    ip_two_headers = {"X-Forwarded-For": "198.51.100.22"}

    assert client.get("/ok", headers=ip_one_headers).status_code == 200
    assert client.get("/ok", headers=ip_two_headers).status_code == 200
    assert client.get("/ok", headers=ip_one_headers).status_code == 429
    assert client.get("/ok", headers=ip_two_headers).status_code == 429
