"""Authentication: API key, Bearer JWT, and DB API tokens."""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_settings
from app.db.session import get_db
from app.services.auth_service import (
    decode_access_token,
    validate_api_token,
)
from sqlalchemy.ext.asyncio import AsyncSession

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
admin_api_key_header = APIKeyHeader(name="X-Admin-API-Key", auto_error=False)
bearer_scheme = HTTPBearer(auto_error=False)


async def _verify_bearer_or_api_key(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    api_key: Annotated[str | None, Depends(api_key_header)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> str | None:
    """Returns auth label on success, None if no valid auth."""
    settings = get_settings()

    # 1. Bearer JWT (session from login)
    if credentials and credentials.credentials:
        token = credentials.credentials
        payload = decode_access_token(token)
        if payload and payload.get("sub"):
            return "authenticated"

    # 2. X-API-Key: env key (legacy)
    if api_key:
        if not settings.api_key:
            return "dev"
        if api_key == settings.api_key:
            return "authenticated"
        # 3. X-API-Key: sk_* token from DB
        if api_key.startswith("sk_"):
            user = await validate_api_token(db, api_key)
            if user:
                return "authenticated"

    return None


async def verify_api_key(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    api_key: Annotated[str | None, Depends(api_key_header)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> str:
    """Verify standard API access. Accepts Bearer JWT, X-API-Key (env or DB token)."""
    result = await _verify_bearer_or_api_key(request, credentials, api_key, db)
    if result:
        return result
    settings = get_settings()
    if not settings.api_key:
        return "dev"
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing API key",
    )


async def verify_admin_api_key(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    admin_key: Annotated[str | None, Depends(admin_api_key_header)],
    api_key: Annotated[str | None, Depends(api_key_header)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> str:
    """Verify admin access. Accepts Bearer JWT (role=admin), X-Admin-API-Key, X-API-Key, or sk_* with admin user."""
    settings = get_settings()

    # 1. Bearer JWT
    if credentials and credentials.credentials:
        payload = decode_access_token(credentials.credentials)
        if payload and payload.get("sub") and payload.get("role") == "admin":
            return "admin"

    # 2. Env keys
    key = admin_key or api_key
    if key:
        if not settings.admin_api_key:
            return "admin_dev"
        if key == settings.admin_api_key or key == settings.api_key:
            return "admin"
        # 3. sk_* token
        if key.startswith("sk_"):
            user = await validate_api_token(db, key)
            if user and user.role == "admin":
                return "admin"

    if not settings.admin_api_key:
        return "admin_dev"
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing admin API key",
    )
