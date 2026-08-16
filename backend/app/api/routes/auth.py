"""Authentication: register, login, profile, password change."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, current_user, db_session, rate_limit
from app.core.config import settings
from app.core.security import (Role, create_access_token, hash_password,
                               verify_password)
from app.models.user import User
from app.models.user_data import Watchlist
from app.services import audit

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=200)
    display_name: str = Field(default="", max_length=120)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_minutes: int
    user: dict


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10, max_length=200)


class NotificationPrefs(BaseModel):
    notify_in_app: Optional[bool] = None
    notify_email: Optional[bool] = None
    notify_telegram: Optional[bool] = None
    notify_browser_push: Optional[bool] = None
    telegram_chat_id: Optional[str] = None


def _user_payload(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role,
        "notifications": {
            "in_app": user.notify_in_app,
            "email": user.notify_email,
            "telegram": user.notify_telegram,
            "browser_push": user.notify_browser_push,
        },
    }


@router.post("/register", response_model=TokenResponse,
             status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(rate_limit("register", 10))])
def register(payload: RegisterRequest, request: Request,
             db: Session = Depends(db_session)) -> TokenResponse:
    existing = db.execute(
        select(User).where(User.email == payload.email.lower())
    ).scalars().first()
    if existing:
        # Same message either way - do not confirm which addresses exist.
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Registration could not be completed.")

    first_user = db.execute(select(User).limit(1)).scalars().first() is None

    user = User(
        email=payload.email.lower(),
        display_name=payload.display_name or payload.email.split("@")[0],
        password_hash=hash_password(payload.password),
        role=Role.ADMIN.value if first_user else Role.USER.value,
    )
    db.add(user)
    db.flush()

    db.add(Watchlist(user_id=user.id, name="Monitoring",
                     description="Default watchlist"))

    audit.record(
        db, action="USER_REGISTERED", entity_type="user", entity_id=user.id,
        actor_id=user.id, actor_email=user.email, actor_role=user.role,
        new_value={"email": user.email, "role": user.role},
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent"),
        reason="First user is created as ADMIN" if first_user else None,
    )
    db.commit()

    return TokenResponse(
        access_token=create_access_token(user.id, user.role),
        expires_in_minutes=settings.access_token_expire_minutes,
        user=_user_payload(user),
    )


@router.post("/login", response_model=TokenResponse,
             dependencies=[Depends(rate_limit("login", 20))])
def login(payload: LoginRequest, request: Request,
          db: Session = Depends(db_session)) -> TokenResponse:
    user = db.execute(
        select(User).where(User.email == payload.email.lower())
    ).scalars().first()

    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            "Email or password is incorrect.")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account is disabled.")

    user.last_login_at = datetime.now(tz=timezone.utc)
    audit.record(
        db, action="USER_LOGIN", entity_type="user", entity_id=user.id,
        actor_id=user.id, actor_email=user.email, actor_role=user.role,
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    db.commit()

    return TokenResponse(
        access_token=create_access_token(user.id, user.role),
        expires_in_minutes=settings.access_token_expire_minutes,
        user=_user_payload(user),
    )


@router.get("/me")
def me(user: User = Depends(current_user)) -> dict:
    return _user_payload(user)


@router.post("/password")
def change_password(payload: PasswordChangeRequest, request: Request,
                    user: User = Depends(current_user),
                    db: Session = Depends(db_session)) -> dict:
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Current password is incorrect.")
    user.password_hash = hash_password(payload.new_password)
    audit.record(
        db, action="PASSWORD_CHANGED", entity_type="user", entity_id=user.id,
        actor_id=user.id, actor_email=user.email, actor_role=user.role,
        ip_address=client_ip(request),
    )
    db.commit()
    return {"status": "ok",
            "message": "Password updated. Existing tokens remain valid until "
                       "they expire."}


@router.patch("/notifications")
def update_notifications(payload: NotificationPrefs,
                         user: User = Depends(current_user),
                         db: Session = Depends(db_session)) -> dict:
    for field_name, value in payload.model_dump(exclude_none=True).items():
        setattr(user, field_name, value)
    db.commit()
    return _user_payload(user)
