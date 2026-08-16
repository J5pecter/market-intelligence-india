"""Declarative base and shared column mixins."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, String, TypeDecorator
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def new_id() -> str:
    return str(uuid.uuid4())


class UTCDateTime(TypeDecorator):
    """SQLite drops tzinfo. This normalises everything to aware UTC on the way
    in and on the way out, so freshness maths never silently compares a naive
    datetime with an aware one."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return value

    def process_result_value(self, value: Any, dialect: Any) -> Any:
        if isinstance(value, datetime) and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class Base(DeclarativeBase):
    pass


class UUIDPrimaryKey:
    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=new_id, index=True
    )


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class Provenance:
    """Attached to every table that stores externally sourced values.

    Without these four columns a row cannot be rendered - the UI refuses to
    display a number whose origin it cannot name.
    """

    provider: Mapped[str] = mapped_column(String(40), default="manual", nullable=False)
    source_name: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(1000))
    data_status: Mapped[str] = mapped_column(String(20), default="MANUAL", nullable=False)
    observed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    is_demo: Mapped[bool] = mapped_column(default=False, nullable=False)
