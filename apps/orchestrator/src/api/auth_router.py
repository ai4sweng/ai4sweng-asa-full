"""Auth endpoints: register, login, and current-user info.

Users are persisted in PostgreSQL via shared.persistence (UserRecord / 0002 migration).
Passwords are hashed with bcrypt directly.
JWTs are signed with the jwt_secret_key from shared.config.
"""

from __future__ import annotations

import asyncio

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger
from sqlalchemy.exc import IntegrityError

from shared.auth.dependencies import get_current_user
from shared.auth.jwt_handler import create_access_token
from shared.auth.schemas import LoginRequest, RegisterRequest, TokenResponse, UserInfo
from shared.config import get_settings
from shared.persistence.session_provider import SessionProvider

router = APIRouter(prefix="/auth", tags=["auth"])


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


# Process-wide SessionProvider for the users table
_sp: SessionProvider | None = None
_sp_lock = asyncio.Lock()


async def _get_sp() -> SessionProvider:
    global _sp
    async with _sp_lock:
        if _sp is None:
            _sp = SessionProvider()
    return _sp


@router.post("/register", response_model=UserInfo, status_code=201)
async def register(req: RegisterRequest):
    """Create a new user account.

    Returns 409 if the username or email is already taken.
    """
    sp = await _get_sp()
    hashed = _hash_password(req.password)
    try:
        async with sp.session_scope() as repo:
            user = await repo.create_user(
                username=req.username,
                hashed_password=hashed,
                email=req.email,
            )
        logger.info("New user registered: {}", req.username)
        return UserInfo(user_id=user.id, username=user.username)
    except IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username or email is already registered",
        )


@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest):
    """Authenticate with username + password; return a signed JWT.

    Returns 401 on bad credentials regardless of which field is wrong
    (no username enumeration).
    """
    sp = await _get_sp()
    async with sp.read_scope() as repo:
        user = await repo.get_user_by_username(req.username)

    if user is None or not _verify_password(req.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabled",
        )

    cfg = get_settings()
    token = create_access_token(user.id, user.username)
    logger.info("User '{}' logged in", user.username)
    return TokenResponse(
        access_token=token,
        expires_in=cfg.jwt_expire_minutes * 60,
    )


@router.get("/me", response_model=UserInfo)
async def me(current_user: UserInfo = Depends(get_current_user)):
    """Return the authenticated user's identity (token validation check)."""
    return current_user
