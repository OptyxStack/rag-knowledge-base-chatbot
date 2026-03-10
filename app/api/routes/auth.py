"""Auth routes: login, me, API token CRUD."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.db.models import ApiToken, User, UserRole
from app.services.auth_service import (
    create_access_token,
    decode_access_token,
    generate_api_token,
    verify_password,
    get_user_by_username,
)

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


class MeResponse(BaseModel):
    id: str
    username: str
    email: str | None
    role: str


class ApiTokenCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)


class ApiTokenResponse(BaseModel):
    id: str
    name: str
    token_prefix: str
    scopes: str
    expires_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime


class ApiTokenCreateResponse(BaseModel):
    id: str
    name: str
    token: str  # Plain token - shown only once
    token_prefix: str
    scopes: str
    expires_at: datetime | None
    created_at: datetime


def _user_from_token(token: str) -> dict | None:
    payload = decode_access_token(token)
    if not payload or not payload.get("sub"):
        return None
    return {"sub": payload["sub"], "role": payload.get("role", "user")}


@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Login with username/password. Returns JWT for session auth."""
    user = await get_user_by_username(db, body.username)
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    token = create_access_token(str(user.id), user.role)
    return LoginResponse(
        access_token=token,
        user={
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "role": user.role,
        },
    )


_bearer = HTTPBearer(auto_error=False)


async def get_current_user_jwt(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer token required",
        )
    payload = _user_from_token(credentials.credentials)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
    result = await db.execute(select(User).where(User.id == payload["sub"]))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )
    return user


@router.get("/me", response_model=MeResponse)
async def me(user: Annotated[User, Depends(get_current_user_jwt)]):
    """Get current user. Requires Bearer JWT from /auth/login."""
    return MeResponse(id=user.id, username=user.username, email=user.email, role=user.role)


def require_admin(user: Annotated[User, Depends(get_current_user_jwt)]) -> User:
    if user.role != UserRole.ADMIN.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin required")
    return user


@router.get("/tokens", response_model=list[ApiTokenResponse])
async def list_tokens(
    user: Annotated[User, Depends(get_current_user_jwt)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """List API tokens for current user."""
    result = await db.execute(
        select(ApiToken).where(ApiToken.user_id == user.id).order_by(ApiToken.created_at.desc())
    )
    tokens = result.scalars().all()
    return [
        ApiTokenResponse(
            id=t.id,
            name=t.name,
            token_prefix=t.token_prefix,
            scopes=t.scopes,
            expires_at=t.expires_at,
            last_used_at=t.last_used_at,
            created_at=t.created_at,
        )
        for t in tokens
    ]


@router.post("/tokens", response_model=ApiTokenCreateResponse)
async def create_token(
    body: ApiTokenCreate,
    user: Annotated[User, Depends(get_current_user_jwt)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Create API token. Plain token returned only once - copy it now."""
    plain, token_hash, prefix = generate_api_token()
    token = ApiToken(
        user_id=user.id,
        name=body.name,
        token_hash=token_hash,
        token_prefix=prefix,
        scopes="api",
    )
    db.add(token)
    await db.flush()
    return ApiTokenCreateResponse(
        id=token.id,
        name=token.name,
        token=plain,
        token_prefix=prefix,
        scopes=token.scopes,
        expires_at=token.expires_at,
        created_at=token.created_at,
    )


@router.delete("/tokens/{token_id}")
async def revoke_token(
    token_id: str,
    user: Annotated[User, Depends(get_current_user_jwt)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Revoke (delete) an API token."""
    result = await db.execute(
        select(ApiToken).where(ApiToken.id == token_id, ApiToken.user_id == user.id)
    )
    token = result.scalar_one_or_none()
    if not token:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Token not found")
    await db.delete(token)
