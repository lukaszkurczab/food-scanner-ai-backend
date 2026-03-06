from typing import Any


class Base: ...


class Certificate(Base):
    def __init__(self, cert: str | dict[str, Any]) -> None: ...
