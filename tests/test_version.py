from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app

client = TestClient(app)


def test_api_version() -> None:
    response = client.get("/api/v1/version")

    assert response.status_code == 200
    data = response.json()
    assert data["version"] == settings.VERSION
