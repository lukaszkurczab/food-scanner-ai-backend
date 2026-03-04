from pytest_mock import MockerFixture

from app.db import firebase


def test_normalize_private_key_supports_double_escaped_newlines() -> None:
    raw = "-----BEGIN PRIVATE KEY-----\\\\nsecret\\\\n-----END PRIVATE KEY-----\\\\n"
    normalized = firebase._normalize_firebase_private_key(raw)
    assert normalized == "-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----\n"


def test_get_firestore_uses_initialized_firebase_app(mocker) -> None:
    firebase.get_firestore.cache_clear()

    app = object()
    client = object()

    init_firebase = mocker.patch("app.db.firebase.init_firebase", return_value=app)
    firestore_client = mocker.patch(
        "app.db.firebase.admin_firestore.client",
        return_value=client,
    )

    result = firebase.get_firestore()

    init_firebase.assert_called_once_with()
    firestore_client.assert_called_once_with(app=app)
    assert result is client

    firebase.get_firestore.cache_clear()


def test_init_firebase_prefers_inline_service_account_credentials(
    mocker: MockerFixture,
) -> None:
    certificate = object()
    initialized_app = object()

    mocker.patch.object(firebase.firebase_admin, "_apps", [])
    mocker.patch.object(firebase.settings, "FIREBASE_PROJECT_ID", "demo-project")
    mocker.patch.object(firebase.settings, "FIREBASE_STORAGE_BUCKET", "")
    mocker.patch.object(firebase.settings, "FIREBASE_CLIENT_EMAIL", "firebase@example.com")
    mocker.patch.object(
        firebase.settings,
        "FIREBASE_PRIVATE_KEY",
        "-----BEGIN PRIVATE KEY-----\\nsecret\\n-----END PRIVATE KEY-----\\n",
    )
    mocker.patch.object(firebase.settings, "GOOGLE_APPLICATION_CREDENTIALS", "")
    certificate_factory = mocker.patch(
        "app.db.firebase.credentials.Certificate",
        return_value=certificate,
    )
    initialize_app = mocker.patch(
        "app.db.firebase.firebase_admin.initialize_app",
        return_value=initialized_app,
    )

    result = firebase.init_firebase()

    certificate_factory.assert_called_once_with(
        {
            "type": "service_account",
            "project_id": "demo-project",
            "client_email": "firebase@example.com",
            "private_key": "-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----\n",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    )
    initialize_app.assert_called_once_with(
        credential=certificate,
        options={
            "projectId": "demo-project",
            "storageBucket": "demo-project.appspot.com",
        },
    )
    assert result is initialized_app


def test_init_firebase_falls_back_to_service_account_file(mocker: MockerFixture) -> None:
    certificate = object()
    initialized_app = object()

    mocker.patch.object(firebase.firebase_admin, "_apps", [])
    mocker.patch.object(firebase.settings, "FIREBASE_PROJECT_ID", "demo-project")
    mocker.patch.object(firebase.settings, "FIREBASE_STORAGE_BUCKET", "")
    mocker.patch.object(firebase.settings, "FIREBASE_CLIENT_EMAIL", "")
    mocker.patch.object(firebase.settings, "FIREBASE_PRIVATE_KEY", "")
    mocker.patch.object(
        firebase.settings,
        "GOOGLE_APPLICATION_CREDENTIALS",
        "/app/service-account.json",
    )
    certificate_factory = mocker.patch(
        "app.db.firebase.credentials.Certificate",
        return_value=certificate,
    )
    initialize_app = mocker.patch(
        "app.db.firebase.firebase_admin.initialize_app",
        return_value=initialized_app,
    )

    result = firebase.init_firebase()

    certificate_factory.assert_called_once_with("/app/service-account.json")
    initialize_app.assert_called_once_with(
        credential=certificate,
        options={
            "projectId": "demo-project",
            "storageBucket": "demo-project.appspot.com",
        },
    )
    assert result is initialized_app
