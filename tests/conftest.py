from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pytest_mock import MockerFixture


@pytest.fixture(autouse=True)
def mock_auth_token_decoder(mocker: MockerFixture) -> MagicMock:
    def _decode(id_token: str) -> dict[str, str]:
        return {"uid": id_token.strip()}

    return mocker.patch(
        "app.api.deps.auth.decode_firebase_token",
        side_effect=_decode,
    )


@pytest.fixture
def auth_headers() -> Callable[[str], dict[str, str]]:
    def _build(uid: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {uid}"}

    return _build


_LEGACY_AI_EXTRA_FILES = {
    "test_api_chat_threads.py",
    "test_openai_service.py",
    "test_text_meal_service.py",
}


def _is_legacy_ai_file(path: Path) -> bool:
    if path.name.startswith("test_ai_"):
        return True
    return path.name in _LEGACY_AI_EXTRA_FILES


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        path = Path(str(item.fspath))
        path_posix = path.as_posix()
        if "/app/tests/" in path_posix:
            continue
        if _is_legacy_ai_file(path):
            item.add_marker(pytest.mark.legacy_ai)
