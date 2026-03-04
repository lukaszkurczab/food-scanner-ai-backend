from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from app.core.exceptions import FirestoreServiceError
from app.main import app
from app.services.feedback_service import FeedbackValidationError

client = TestClient(app)


def test_post_feedback_returns_created_payload(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    create_feedback = mocker.patch(
        "app.api.routes.feedback.feedback_service.create_feedback",
        return_value={
            "id": "feedback-1",
            "message": "App is great",
            "userUid": "user-1",
            "email": "user@example.com",
            "deviceInfo": {
                "modelName": "iPhone",
                "osName": "iOS",
                "osVersion": "18",
            },
            "createdAt": 1,
            "updatedAt": 2,
            "status": "new",
            "attachmentUrl": "https://cdn/feedback.jpg",
            "attachmentPath": "feedback/user-1/feedback-1/feedback.jpg",
        },
    )

    response = client.post(
        "/api/v1/users/me/feedback",
        data={
            "message": "App is great",
            "deviceModelName": "iPhone",
            "deviceOsName": "iOS",
            "deviceOsVersion": "18",
        },
        files={"file": ("feedback.jpg", b"image", "image/jpeg")},
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "feedback": {
            "id": "feedback-1",
            "message": "App is great",
            "userUid": "user-1",
            "email": "user@example.com",
            "deviceInfo": {
                "modelName": "iPhone",
                "osName": "iOS",
                "osVersion": "18",
            },
            "createdAt": 1,
            "updatedAt": 2,
            "status": "new",
            "attachmentUrl": "https://cdn/feedback.jpg",
            "attachmentPath": "feedback/user-1/feedback-1/feedback.jpg",
        },
        "created": True,
    }
    create_feedback.assert_called_once_with(
        user_id="user-1",
        message="App is great",
        email=None,
        device_info={
            "modelName": "iPhone",
            "osName": "iOS",
            "osVersion": "18",
        },
        attachment=mocker.ANY,
    )


def test_post_feedback_returns_400_for_invalid_payload(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch(
        "app.api.routes.feedback.feedback_service.create_feedback",
        side_effect=FeedbackValidationError("Feedback message is required."),
    )

    response = client.post(
        "/api/v1/users/me/feedback",
        data={"message": ""},
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Feedback message is required."}


def test_post_feedback_returns_500_for_firestore_errors(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch(
        "app.api.routes.feedback.feedback_service.create_feedback",
        side_effect=FirestoreServiceError("boom"),
    )

    response = client.post(
        "/api/v1/users/me/feedback",
        data={"message": "App is great"},
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "Database error"}
