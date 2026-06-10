"""FastAPI dependency that validates Bearer JWTs on protected endpoints."""

from __future__ import annotations

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .jwt_handler import decode_token
from .schemas import UserInfo

_bearer = HTTPBearer(auto_error=True)


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


# Alias for explicit Depends usage in router signatures
require_auth = Depends(get_current_user)
