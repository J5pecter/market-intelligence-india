"""Audit logs, provider registry/status, job runs and compliance documents."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (Boolean, Float, Index, Integer, String, Text)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamped, UTCDateTime, UUIDPrimaryKey


class AuditLog(Base, UUIDPrimaryKey, Timestamped):
    """Append-only. Nothing in this table is ever updated or deleted by the app."""

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_entity", "entity_type", "entity_id"),
        Index("ix_audit_time", "created_at"),
    )

    actor_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)
    actor_email: Mapped[Optional[str]] = mapped_column(String(255))
    actor_role: Mapped[Optional[str]] = mapped_column(String(30))

    action: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_id: Mapped[Optional[str]] = mapped_column(String(64))

    old_value: Mapped[Optional[str]] = mapped_column(Text)
    new_value: Mapped[Optional[str]] = mapped_column(Text)
    reason: Mapped[Optional[str]] = mapped_column(String(1000))

    ip_address: Mapped[Optional[str]] = mapped_column(String(64))
    user_agent: Mapped[Optional[str]] = mapped_column(String(500))
    request_id: Mapped[Optional[str]] = mapped_column(String(64))


class DataProviderStatus(Base, UUIDPrimaryKey, Timestamped):
    """One row per configured provider - drives the system-health page."""

    __tablename__ = "data_provider_status"

    name: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)
    provider_type: Mapped[str] = mapped_column(String(40), default="MARKET_DATA")
    base_url: Mapped[Optional[str]] = mapped_column(String(400))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    requires_auth: Mapped[bool] = mapped_column(Boolean, default=False)
    rate_limit_per_minute: Mapped[Optional[int]] = mapped_column(Integer)
    refresh_seconds: Mapped[Optional[int]] = mapped_column(Integer)
    licence: Mapped[Optional[str]] = mapped_column(String(300))
    terms_url: Mapped[Optional[str]] = mapped_column(String(500))
    is_delayed: Mapped[bool] = mapped_column(Boolean, default=True)
    reliability: Mapped[str] = mapped_column(String(10), default="UNKNOWN")

    status: Mapped[str] = mapped_column(String(20), default="UNKNOWN")
    circuit_state: Mapped[str] = mapped_column(String(12), default="CLOSED")
    last_success_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    last_failure_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    last_error: Mapped[Optional[str]] = mapped_column(String(1000))
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    calls_last_hour: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[Optional[str]] = mapped_column(Text)


class JobRunLog(Base, UUIDPrimaryKey, Timestamped):
    __tablename__ = "job_runs"
    __table_args__ = (Index("ix_job_name_time", "job_name", "started_at"),)

    job_name: Mapped[str] = mapped_column(String(80), nullable=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    finished_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    duration_ms: Mapped[Optional[float]] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(12), default="RUNNING")
    provider: Mapped[Optional[str]] = mapped_column(String(60))
    records_received: Mapped[int] = mapped_column(Integer, default=0)
    records_saved: Mapped[int] = mapped_column(Integer, default=0)
    records_rejected: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[Optional[str]] = mapped_column(Text)


class ComplianceDocument(Base, UUIDPrimaryKey, Timestamped):
    """Tracked regulatory sources. Requirements are data, reviewed by a human -
    the platform does not hard-code the rule text."""

    __tablename__ = "compliance_documents"

    name: Mapped[str] = mapped_column(String(300), nullable=False)
    url: Mapped[Optional[str]] = mapped_column(String(800))
    regulator: Mapped[str] = mapped_column(String(40), default="SEBI")
    document_type: Mapped[str] = mapped_column(String(60), default="REGULATION")
    published_date: Mapped[Optional[str]] = mapped_column(String(20))
    effective_date: Mapped[Optional[str]] = mapped_column(String(20))
    version: Mapped[Optional[str]] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="UNVERIFIED")
    last_checked_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    checked_by: Mapped[Optional[str]] = mapped_column(String(120))
    summary: Mapped[Optional[str]] = mapped_column(Text)
    applies_to: Mapped[Optional[str]] = mapped_column(String(300))


class ScannerDefinition(Base, UUIDPrimaryKey, Timestamped):
    """Saved scanner. Built-ins are seeded; users can save their own."""

    __tablename__ = "scanner_definitions"

    key: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(20), default="TECHNICAL")
    description: Mapped[str] = mapped_column(Text, default="")
    filters_json: Mapped[str] = mapped_column(Text, default="[]")
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    owner_id: Mapped[Optional[str]] = mapped_column(String(36))
    methodology_note: Mapped[Optional[str]] = mapped_column(Text)
