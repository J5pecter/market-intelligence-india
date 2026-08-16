"""Users, roles and sessions."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamped, UTCDateTime, UUIDPrimaryKey


class Role(Base, UUIDPrimaryKey, Timestamped):
    __tablename__ = "roles"

    name: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(String(300), default="")


class User(Base, UUIDPrimaryKey, Timestamped):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, index=True,
                                       nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), default="")
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(30), default="USER", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)

    # Per-user notification preferences; channels are opt-in, all default off.
    notify_in_app: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_email: Mapped[bool] = mapped_column(Boolean, default=False)
    notify_telegram: Mapped[bool] = mapped_column(Boolean, default=False)
    notify_browser_push: Mapped[bool] = mapped_column(Boolean, default=False)
    telegram_chat_id: Mapped[Optional[str]] = mapped_column(String(64))

    watchlists: Mapped[List["Watchlist"]] = relationship(  # noqa: F821
        back_populates="user", cascade="all, delete-orphan"
    )


class ApiCredential(Base, UUIDPrimaryKey, Timestamped):
    """Provider credentials, encrypted at rest. Never serialised to clients."""

    __tablename__ = "api_credentials"
    __table_args__ = (UniqueConstraint("owner_id", "provider", name="uq_cred_owner_provider"),)

    owner_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    provider: Mapped[str] = mapped_column(String(60), nullable=False)
    label: Mapped[str] = mapped_column(String(120), default="")
    encrypted_key: Mapped[str] = mapped_column(String(2000), nullable=False)
    encrypted_secret: Mapped[Optional[str]] = mapped_column(String(2000))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
