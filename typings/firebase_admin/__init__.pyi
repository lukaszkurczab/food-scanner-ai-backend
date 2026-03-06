from typing import Any

from . import auth, credentials, firestore, storage


class App: ...


_apps: dict[str, App]


def initialize_app(
    credential: credentials.Base | None = ...,
    options: dict[str, Any] | None = ...,
    name: str = ...,
) -> App: ...


def get_app(name: str = ...) -> App: ...
