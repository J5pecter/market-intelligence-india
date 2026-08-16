"""Append-only audit logging.

Nothing in this module updates or deletes an audit row. Published research is
never silently rewritten: `record_change` captures the before/after of every
field the caller changed, plus who changed it and why.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Dict, Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.system import AuditLog


def _serialise(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def diff(before: Dict[str, Any], after: Dict[str, Any],
         fields: Optional[Iterable[str]] = None) -> Dict[str, Dict[str, Any]]:
    keys = set(fields) if fields else set(before) | set(after)
    changes: Dict[str, Dict[str, Any]] = {}
    for key in keys:
        old, new = before.get(key), after.get(key)
        if old != new:
            changes[key] = {"from": _serialise(old), "to": _serialise(new)}
    return changes


def record(
    db: Session,
    *,
    action: str,
    entity_type: str,
    entity_id: Optional[str] = None,
    actor_id: Optional[str] = None,
    actor_email: Optional[str] = None,
    actor_role: Optional[str] = None,
    old_value: Any = None,
    new_value: Any = None,
    reason: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    request_id: Optional[str] = None,
) -> AuditLog:
    entry = AuditLog(
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        actor_id=actor_id,
        actor_email=actor_email,
        actor_role=actor_role,
        old_value=json.dumps(old_value, default=_serialise)
        if old_value is not None else None,
        new_value=json.dumps(new_value, default=_serialise)
        if new_value is not None else None,
        reason=reason,
        ip_address=ip_address,
        user_agent=(user_agent or "")[:500] or None,
        request_id=request_id,
    )
    db.add(entry)
    db.flush()
    return entry


def record_change(
    db: Session, *, entity_type: str, entity_id: str,
    before: Dict[str, Any], after: Dict[str, Any],
    actor: Optional[Any] = None, reason: Optional[str] = None,
    action: str = "UPDATE", **kwargs: Any,
) -> Optional[AuditLog]:
    changes = diff(before, after)
    if not changes:
        return None
    return record(
        db, action=action, entity_type=entity_type, entity_id=entity_id,
        actor_id=getattr(actor, "id", None),
        actor_email=getattr(actor, "email", None),
        actor_role=getattr(actor, "role", None),
        old_value={k: v["from"] for k, v in changes.items()},
        new_value={k: v["to"] for k, v in changes.items()},
        reason=reason, **kwargs,
    )


def history(db: Session, entity_type: str, entity_id: str,
            limit: int = 100) -> list[AuditLog]:
    return list(db.execute(
        select(AuditLog)
        .where(AuditLog.entity_type == entity_type)
        .where(AuditLog.entity_id == entity_id)
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
    ).scalars().all())
