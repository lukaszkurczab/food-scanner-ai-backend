from typing import Any


class InvalidIdTokenError(Exception): ...


class ExpiredIdTokenError(Exception): ...


class RevokedIdTokenError(Exception): ...


class UserDisabledError(Exception): ...


class CertificateFetchError(Exception): ...


def verify_id_token(
    id_token: str,
    app: Any | None = ...,
    check_revoked: bool = ...,
    clock_skew_seconds: int = ...,
) -> dict[str, Any]: ...
