import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/api/v1/ai/ask", {"message": "Suggest a dinner"}),
        ("/api/v1/ai/text-meal/analyze", {"payload": {"name": "burger"}}),
        ("/api/v1/ai/photo/analyze", {"imageBase64": "base64-image"}),
    ],
)
def test_ai_endpoints_require_authentication(path: str, payload: dict[str, object]) -> None:
    response = client.post(path, json=payload)

    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required"}
    assert response.headers.get("WWW-Authenticate") == "Bearer"
