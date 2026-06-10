"""shared.auth — JWT creation/validation and FastAPI dependency."""

from .dependencies import get_current_user, require_auth
from .jwt_handler import create_access_token, decode_token
from .schemas import LoginRequest, RegisterRequest, TokenResponse, UserInfo

__all__ = [
    "create_access_token",
    "decode_token",
    "get_current_user",
    "require_auth",
    "LoginRequest",
    "RegisterRequest",
    "TokenResponse",
    "UserInfo",
]
