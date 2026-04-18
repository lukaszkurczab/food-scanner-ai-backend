from collections.abc import Callable
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
