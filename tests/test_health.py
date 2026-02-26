from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_check() -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "caloriai-backend"
    assert "timestamp" in data


def test_health_check_without_version_not_found() -> None:
    response = client.get("/health")

    assert response.status_code == 404
