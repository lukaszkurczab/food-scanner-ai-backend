from dataclasses import dataclass
from typing import Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from firebase_admin import auth as firebase_auth
from firebase_admin.exceptions import FirebaseError

from app.db.firebase import init_firebase

_http_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AuthenticatedUser:
    uid: str
    claims: dict[str, Any]


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def decode_firebase_token(id_token: str) -> dict[str, Any]:
    try:
        firebase_app = init_firebase()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service unavailable",
        ) from exc

    try:
        return firebase_auth.verify_id_token(id_token, app=firebase_app)
    except (
        firebase_auth.InvalidIdTokenError,
        firebase_auth.ExpiredIdTokenError,
        firebase_auth.RevokedIdTokenError,
        firebase_auth.UserDisabledError,
        ValueError,
    ) as exc:
        raise _unauthorized("Invalid authentication credentials") from exc
    except (firebase_auth.CertificateFetchError, FirebaseError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service unavailable",
        ) from exc


def _build_authenticated_user(
    credentials: HTTPAuthorizationCredentials | None,
) -> AuthenticatedUser | None:
    if credentials is None:
        return None

    scheme = (credentials.scheme or "").lower()
    token = credentials.credentials.strip()
    if scheme != "bearer" or not token:
        raise _unauthorized("Authentication required")

    claims = decode_firebase_token(token)
    uid = str(claims.get("uid") or "").strip()
    if not uid:
        raise _unauthorized("Invalid authentication credentials")

    return AuthenticatedUser(uid=uid, claims=claims)


def get_required_authenticated_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_http_bearer),
) -> AuthenticatedUser:
    user = _build_authenticated_user(credentials)
    if user is None:
        raise _unauthorized("Authentication required")
    return user


def get_optional_authenticated_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_http_bearer),
) -> AuthenticatedUser | None:
    return _build_authenticated_user(credentials)


def ensure_authenticated_user_matches(
    current_user: AuthenticatedUser,
    requested_user_id: str,
) -> str:
    normalized_user_id = requested_user_id.strip()
    if not normalized_user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing user ID",
        )

    if current_user.uid != normalized_user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden",
        )

    return current_user.uid
