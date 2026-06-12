"""FastAPI dependency that validates Bearer JWTs on protected endpoints."""

from __future__ import annotations

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .jwt_handler import decode_token
from .schemas import UserInfo

_bearer = HTTPBearer(auto_error=True)
_bearer_optional = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
) -> UserInfo:
    """Validate the Bearer token and return the authenticated user.

    Raises 401 if the token is missing, expired, or tampered with.
    """
    try:
        payload = decode_token(credentials.credentials)
        return UserInfo(user_id=payload["user_id"], username=payload["sub"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except (jwt.PyJWTError, KeyError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_current_user_optional(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_optional),
) -> UserInfo | None:
    """Like get_current_user but returns None instead of 401 when no Bearer
    header is present.

    Used by the SSE /events endpoint: the browser EventSource API cannot send an
    Authorization header, so those clients authenticate via a ``?token=`` query
    param instead. A non-erroring dependency lets the endpoint fall back to it.
    """
    if credentials is None:
        return None
    return await get_current_user(credentials)


# Alias for explicit Depends usage in router signatures
require_auth = Depends(get_current_user)
