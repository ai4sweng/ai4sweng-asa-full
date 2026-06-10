"""JWT creation and decoding using PyJWT + shared.config settings."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt

from shared.config import get_settings


def create_access_token(user_id: str, username: str) -> str:
    """Return a signed JWT with sub/user_id claims and exp set from config."""
    cfg = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": username,
        "user_id": user_id,
        "iat": now,
        "exp": now + timedelta(minutes=cfg.jwt_expire_minutes),
    }
    return jwt.encode(payload, cfg.jwt_secret_key, algorithm=cfg.jwt_algorithm)


def decode_token(token: str) -> dict:
    """Decode and verify a JWT; raises jwt.PyJWTError on failure."""
    cfg = get_settings()
    return jwt.decode(token, cfg.jwt_secret_key, algorithms=[cfg.jwt_algorithm])
