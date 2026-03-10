"""Auth service: password hashing, JWT, API token validation."""

import hashlib
import secrets
from datetime import datetime, timezone, timedelta
from typing import Any

import bcrypt
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import ApiToken, User, UserRole


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_access_token(sub: str, role: str, extra: dict[str, Any] | None = None) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {
        "sub": sub,
        "role": role,
        "iat": now,
        "exp": expire,
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any] | None:
    settings = get_settings()
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError:
        return None


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def token_prefix(token: str) -> str:
    return token[:8] + "..." if len(token) >= 8 else "***"


def generate_api_token() -> tuple[str, str, str]:
    """Generate (plain_token, token_hash, token_prefix)."""
    plain = "sk_" + secrets.token_urlsafe(32)
    return plain, hash_token(plain), token_prefix(plain)


async def get_user_by_username(session: AsyncSession, username: str) -> User | None:
    result = await session.execute(select(User).where(User.username == username))
    return result.scalar_one_or_none()


async def validate_api_token(session: AsyncSession, token: str) -> User | None:
    """Validate API token by hash. Returns user if valid."""
    if not token or not token.startswith("sk_"):
        return None
    token_hash = hash_token(token)
    result = await session.execute(
        select(ApiToken, User)
        .join(User, ApiToken.user_id == User.id)
        .where(ApiToken.token_hash == token_hash)
    )
    row = result.one_or_none()
    if not row:
        return None
    api_token, user = row
    if not user.is_active:
        return None
    if api_token.expires_at and api_token.expires_at < datetime.now(timezone.utc):
        return None
    return user


def is_admin(user: User) -> bool:
    return user.role == UserRole.ADMIN.value
