"""Shared FastAPI dependencies: auth, RBAC, request metadata, rate limiting."""

from __future__ import annotations

import uuid
from typing import Iterator, Optional

import jwt
from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.cache import rate_limit_ok
from app.core.security import Role, decode_access_token, role_allows
from app.db.session import get_db
from app.models.user import User

bearer = HTTPBearer(auto_error=False)


def db_session() -> Iterator[Session]:
    yield from get_db()


def request_id(x_request_id: Optional[str] = Header(default=None)) -> str:
    return x_request_id or str(uuid.uuid4())


def client_ip(request: Request) -> Optional[str]:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def current_user_optional(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
    db: Session = Depends(db_session),
) -> Optional[User]:
    if credentials is None:
        return None
    try:
        payload = decode_access_token(credentials.credentials)
    except jwt.PyJWTError:
        return None
    user = db.execute(
        select(User).where(User.id == payload.get("sub"))
    ).scalars().first()
    return user if user and user.is_active else None


def current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
    db: Session = Depends(db_session),
) -> User:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_access_token(credentials.credentials)
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            "Session expired. Sign in again.") from exc
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            "Invalid credentials.") from exc

    user = db.execute(
        select(User).where(User.id == payload.get("sub"))
    ).scalars().first()
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            "Account not found or disabled.")
    return user


def require_role(minimum: Role):
    def _dependency(user: User = Depends(current_user)) -> User:
        if not role_allows(user.role, minimum):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"This action requires the {minimum.value} role.",
            )
        return user
    return _dependency


require_admin = require_role(Role.ADMIN)
require_analyst = require_role(Role.ANALYST)


def rate_limit(bucket: str, limit: int = 120):
    """Per-IP fixed-window limiter for public endpoints."""
    def _dependency(request: Request) -> None:
        ip = client_ip(request) or "unknown"
        if not rate_limit_ok(f"{bucket}:{ip}", limit):
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                f"Rate limit reached for {bucket}. Try again in a minute.",
            )
    return _dependency
