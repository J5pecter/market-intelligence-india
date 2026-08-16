"""Admin panel: users, research approval, data entry, compliance, jobs."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, db_session, require_admin, require_analyst
from app.core.compliance import (ComplianceViolation, load_compliance,
                                 save_compliance)
from app.core.config import settings
from app.core.security import Role
from app.models.instrument import Instrument
from app.models.ipo import Ipo, IpoGmpHistory, IpoSubscription
from app.models.market import Quote
from app.models.research import ResearchCall, ResearchSource
from app.models.system import (AuditLog, ComplianceDocument,
                               DataProviderStatus, JobRunLog)
from app.models.user import User
from app.providers.registry import registry
from app.services import audit
from app.services.research_calls import research_call_service

router = APIRouter(prefix="/admin", tags=["admin"])


# --------------------------------------------------------------------------
# Users
# --------------------------------------------------------------------------


@router.get("/users")
def list_users(admin: User = Depends(require_admin),
               db: Session = Depends(db_session)) -> Dict[str, Any]:
    rows = db.execute(select(User).order_by(User.created_at.desc())).scalars().all()
    return {
        "users": [
            {"id": u.id, "email": u.email, "display_name": u.display_name,
             "role": u.role, "is_active": u.is_active,
             "created_at": u.created_at.isoformat(),
             "last_login_at": u.last_login_at.isoformat()
             if u.last_login_at else None}
            for u in rows
        ]
    }


class UserUpdate(BaseModel):
    role: Optional[str] = None
    is_active: Optional[bool] = None
    reason: str = Field(min_length=3)


@router.patch("/users/{user_id}")
def update_user(user_id: str, payload: UserUpdate, request: Request,
                admin: User = Depends(require_admin),
                db: Session = Depends(db_session)) -> Dict[str, Any]:
    user = db.execute(select(User).where(User.id == user_id)).scalars().first()
    if user is None:
        raise HTTPException(404, "User not found.")
    if user.id == admin.id and payload.is_active is False:
        raise HTTPException(400, "You cannot deactivate your own account.")
    if payload.role and payload.role not in [r.value for r in Role]:
        raise HTTPException(400, f"Role must be one of {[r.value for r in Role]}.")

    before = {"role": user.role, "is_active": user.is_active}
    if payload.role:
        user.role = payload.role
    if payload.is_active is not None:
        user.is_active = payload.is_active

    audit.record_change(
        db, entity_type="user", entity_id=user.id, before=before,
        after={"role": user.role, "is_active": user.is_active},
        actor=admin, reason=payload.reason, action="USER_UPDATED",
        ip_address=client_ip(request),
    )
    db.commit()
    return {"id": user.id, "role": user.role, "is_active": user.is_active}


# --------------------------------------------------------------------------
# Research calls
# --------------------------------------------------------------------------


class ResearchCallCreate(BaseModel):
    symbol: str
    company_name: str = ""
    segment: str = "EQUITY"
    side: str = "BUY"
    source_type: str = "EXTERNAL_RESEARCH"
    source_name: str
    source_id: Optional[str] = None
    analyst_name: Optional[str] = None
    original_url: Optional[str] = None
    published_at: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    was_transformed: bool = False
    transformation_note: Optional[str] = None
    original_recommendation: Optional[str] = None
    entry_min: Optional[float] = None
    entry_max: Optional[float] = None
    stop_loss: Optional[float] = None
    target_1: Optional[float] = None
    target_2: Optional[float] = None
    target_3: Optional[float] = None
    expiry: Optional[date] = None
    strike: Optional[float] = None
    option_type: Optional[str] = None
    lot_size: Optional[int] = None
    horizon: Optional[str] = None
    timeframe: Optional[str] = None
    rationale: Optional[str] = None
    invalidation: Optional[str] = None
    why_now: Optional[List[str]] = None
    why_not: Optional[List[str]] = None
    confidence: Optional[float] = None
    risk_rating: Optional[str] = None
    is_published: bool = False
    is_demo: bool = False


@router.post("/research-call", status_code=201)
def create_research_call(payload: ResearchCallCreate, request: Request,
                         analyst: User = Depends(require_analyst),
                         db: Session = Depends(db_session)) -> Dict[str, Any]:
    if payload.is_demo and settings.is_production:
        raise HTTPException(
            400, "Demo records cannot be created in a PRODUCTION deployment."
        )
    data = payload.model_dump()
    data["symbol"] = data["symbol"].upper()
    data["why_now"] = json.dumps(payload.why_now) if payload.why_now else None
    data["why_not"] = json.dumps(payload.why_not) if payload.why_not else None

    try:
        call = research_call_service.create(db, data, actor=analyst)
    except (ValueError, ComplianceViolation) as exc:
        raise HTTPException(400, str(exc)) from exc

    research_call_service.refresh_status(db, call)
    db.commit()
    return {"id": call.id, "symbol": call.symbol, "status": call.status,
            "version": call.version}


class ResearchCallUpdate(BaseModel):
    reason: str = Field(min_length=5)
    changes: Dict[str, Any]


@router.patch("/research-call/{call_id}")
def update_research_call(call_id: str, payload: ResearchCallUpdate,
                         request: Request,
                         analyst: User = Depends(require_analyst),
                         db: Session = Depends(db_session)) -> Dict[str, Any]:
    call = db.execute(
        select(ResearchCall).where(ResearchCall.id == call_id)
    ).scalars().first()
    if call is None:
        raise HTTPException(404, "Research call not found.")
    try:
        research_call_service.update(db, call, payload.changes, analyst,
                                     payload.reason)
    except (ValueError, ComplianceViolation) as exc:
        raise HTTPException(400, str(exc)) from exc
    research_call_service.refresh_status(db, call)
    db.commit()
    return {"id": call.id, "version": call.version, "status": call.status}


@router.post("/research-call/{call_id}/approve")
def approve_research_call(call_id: str, request: Request,
                          admin: User = Depends(require_admin),
                          db: Session = Depends(db_session)) -> Dict[str, Any]:
    call = db.execute(
        select(ResearchCall).where(ResearchCall.id == call_id)
    ).scalars().first()
    if call is None:
        raise HTTPException(404, "Research call not found.")
    before = {"is_published": call.is_published,
              "lifecycle_state": call.lifecycle_state}
    call.is_published = True
    call.approved_by = admin.email
    call.approved_at = datetime.now(tz=timezone.utc)
    call.lifecycle_state = "PUBLISHED"
    audit.record_change(
        db, entity_type="research_call", entity_id=call.id, before=before,
        after={"is_published": True, "lifecycle_state": "PUBLISHED"},
        actor=admin, reason="Approved for publication",
        action="RESEARCH_CALL_APPROVED", ip_address=client_ip(request),
    )
    db.commit()
    return {"id": call.id, "is_published": True}


@router.get("/research-calls/pending")
def pending_calls(analyst: User = Depends(require_analyst),
                  db: Session = Depends(db_session)) -> Dict[str, Any]:
    rows = db.execute(
        select(ResearchCall).where(ResearchCall.is_published.is_(False))
        .order_by(ResearchCall.created_at.desc())
    ).scalars().all()
    return {
        "calls": [
            {"id": c.id, "symbol": c.symbol, "side": c.side,
             "segment": c.segment, "source_type": c.source_type,
             "source_name": c.source_name, "version": c.version,
             "created_at": c.created_at.isoformat(), "is_demo": c.is_demo}
            for c in rows
        ]
    }


# --------------------------------------------------------------------------
# Research sources
# --------------------------------------------------------------------------


class SourceCreate(BaseModel):
    name: str
    source_type: str = "EXTERNAL_RESEARCH"
    organisation: Optional[str] = None
    website: Optional[str] = None
    registration_note: Optional[str] = None
    reliability: str = "UNKNOWN"
    licence_note: Optional[str] = None


@router.get("/sources")
def list_sources(analyst: User = Depends(require_analyst),
                 db: Session = Depends(db_session)) -> Dict[str, Any]:
    rows = db.execute(select(ResearchSource)).scalars().all()
    return {
        "sources": [
            {"id": s.id, "name": s.name, "source_type": s.source_type,
             "organisation": s.organisation, "website": s.website,
             "registration_note": s.registration_note,
             "reliability": s.reliability, "is_active": s.is_active,
             "licence_note": s.licence_note}
            for s in rows
        ]
    }


@router.post("/sources", status_code=201)
def create_source(payload: SourceCreate, admin: User = Depends(require_admin),
                  db: Session = Depends(db_session)) -> Dict[str, Any]:
    source = ResearchSource(**payload.model_dump())
    db.add(source)
    audit.record(db, action="RESEARCH_SOURCE_CREATED",
                 entity_type="research_source", entity_id=source.id,
                 actor_id=admin.id, actor_email=admin.email,
                 new_value=payload.model_dump())
    db.commit()
    return {"id": source.id, "name": source.name}


# --------------------------------------------------------------------------
# Instruments and manual market data
# --------------------------------------------------------------------------


class InstrumentCreate(BaseModel):
    symbol: str
    name: str
    exchange_code: str = "NSE"
    segment: str = "EQUITY"
    isin: Optional[str] = None
    bse_code: Optional[str] = None
    sector: Optional[str] = None
    industry: Optional[str] = None
    lot_size: Optional[int] = None
    is_fno_eligible: bool = False


@router.post("/instruments", status_code=201)
def create_instrument(payload: InstrumentCreate,
                      admin: User = Depends(require_admin),
                      db: Session = Depends(db_session)) -> Dict[str, Any]:
    instrument = Instrument(
        **payload.model_dump(), provider="manual",
        source_name="Operator entry", data_status="MANUAL",
        observed_at=datetime.now(tz=timezone.utc),
    )
    instrument.symbol = instrument.symbol.upper()
    db.add(instrument)
    audit.record(db, action="INSTRUMENT_CREATED", entity_type="instrument",
                 entity_id=instrument.id, actor_id=admin.id,
                 actor_email=admin.email, new_value=payload.model_dump())
    db.commit()
    return {"id": instrument.id, "symbol": instrument.symbol}


class ManualQuote(BaseModel):
    symbol: str
    ltp: float
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    previous_close: Optional[float] = None
    volume: Optional[int] = None
    week52_high: Optional[float] = None
    week52_low: Optional[float] = None
    source_name: str = "Operator entry"
    observed_at: Optional[datetime] = None


@router.post("/quotes")
def upsert_manual_quote(payload: ManualQuote,
                        admin: User = Depends(require_admin),
                        db: Session = Depends(db_session)) -> Dict[str, Any]:
    instrument = db.execute(
        select(Instrument).where(Instrument.symbol == payload.symbol.upper())
    ).scalars().first()
    if instrument is None:
        raise HTTPException(404, "Add the instrument first.")

    quote = db.execute(
        select(Quote).where(Quote.instrument_id == instrument.id)
    ).scalars().first() or Quote(instrument_id=instrument.id,
                                 symbol=instrument.symbol)

    for field_name in ("ltp", "open", "high", "low", "previous_close",
                       "volume", "week52_high", "week52_low"):
        value = getattr(payload, field_name)
        if value is not None:
            setattr(quote, field_name, value)

    if payload.previous_close:
        quote.change = round(payload.ltp - payload.previous_close, 4)
        quote.change_pct = round(
            quote.change / payload.previous_close * 100.0, 4
        )
    quote.provider = "manual"
    quote.source_name = payload.source_name
    quote.data_status = "MANUAL"
    quote.observed_at = payload.observed_at or datetime.now(tz=timezone.utc)
    quote.is_demo = False
    db.add(quote)

    audit.record(db, action="MANUAL_QUOTE_SET", entity_type="quote",
                 entity_id=quote.id, actor_id=admin.id,
                 actor_email=admin.email, new_value=payload.model_dump())
    db.commit()
    return {"symbol": quote.symbol, "ltp": quote.ltp,
            "data_status": quote.data_status}


# --------------------------------------------------------------------------
# IPO data entry
# --------------------------------------------------------------------------


class GmpEntry(BaseModel):
    ipo_id: str
    gmp: float
    observed_on: Optional[datetime] = None
    source_name: str
    source_url: Optional[str] = None
    kostak: Optional[float] = None
    subject_to_sauda: Optional[float] = None


@router.post("/ipo/gmp", status_code=201)
def record_gmp(payload: GmpEntry, admin: User = Depends(require_admin),
               db: Session = Depends(db_session)) -> Dict[str, Any]:
    ipo = db.execute(select(Ipo).where(Ipo.id == payload.ipo_id)).scalars().first()
    if ipo is None:
        raise HTTPException(404, "IPO not found.")
    reference = ipo.price_band_high
    entry = IpoGmpHistory(
        ipo_id=ipo.id,
        observed_on=payload.observed_on or datetime.now(tz=timezone.utc),
        gmp=payload.gmp,
        gmp_pct=round(payload.gmp / reference * 100.0, 2) if reference else None,
        estimated_listing_price=round(reference + payload.gmp, 2)
        if reference else None,
        reference_price=reference,
        kostak=payload.kostak, subject_to_sauda=payload.subject_to_sauda,
        provider="manual", source_name=payload.source_name,
        source_url=payload.source_url, data_status="UNVERIFIED",
    )
    db.add(entry)
    audit.record(db, action="IPO_GMP_RECORDED", entity_type="ipo_gmp",
                 entity_id=ipo.id, actor_id=admin.id, actor_email=admin.email,
                 new_value=payload.model_dump(),
                 reason="Grey-market quote is an unofficial indicator")
    db.commit()
    return {"ipo_id": ipo.id, "gmp": entry.gmp, "gmp_pct": entry.gmp_pct,
            "status": "UNVERIFIED",
            "notice": "Recorded as an unofficial grey-market indicator."}


# --------------------------------------------------------------------------
# Compliance
# --------------------------------------------------------------------------


class ComplianceUpdate(BaseModel):
    config: Dict[str, Any]
    reason: str = Field(min_length=10)


@router.put("/compliance")
def update_compliance(payload: ComplianceUpdate, request: Request,
                      admin: User = Depends(require_admin),
                      db: Session = Depends(db_session)) -> Dict[str, Any]:
    before = load_compliance()
    try:
        updated = save_compliance(payload.config)
    except ComplianceViolation as exc:
        raise HTTPException(400, str(exc)) from exc

    audit.record(
        db, action="COMPLIANCE_CONFIG_UPDATED", entity_type="compliance",
        entity_id="config/compliance.json", actor_id=admin.id,
        actor_email=admin.email, actor_role=admin.role,
        old_value=before, new_value=updated, reason=payload.reason,
        ip_address=client_ip(request),
    )
    db.commit()
    return {"status": "ok", "disclaimer_version": updated.get("disclaimer_version")}


class ComplianceDocumentCreate(BaseModel):
    name: str
    url: Optional[str] = None
    regulator: str = "SEBI"
    document_type: str = "REGULATION"
    published_date: Optional[str] = None
    effective_date: Optional[str] = None
    version: Optional[str] = None
    status: str = "UNVERIFIED"
    summary: Optional[str] = None
    applies_to: Optional[str] = None


@router.post("/compliance/documents", status_code=201)
def add_compliance_document(payload: ComplianceDocumentCreate,
                            admin: User = Depends(require_admin),
                            db: Session = Depends(db_session)) -> Dict[str, Any]:
    document = ComplianceDocument(**payload.model_dump(),
                                  last_checked_at=datetime.now(tz=timezone.utc),
                                  checked_by=admin.email)
    db.add(document)
    audit.record(db, action="COMPLIANCE_DOC_ADDED",
                 entity_type="compliance_document", entity_id=document.id,
                 actor_id=admin.id, actor_email=admin.email,
                 new_value=payload.model_dump())
    db.commit()
    return {"id": document.id, "name": document.name, "status": document.status}


# --------------------------------------------------------------------------
# Providers, jobs and audit
# --------------------------------------------------------------------------


@router.get("/providers")
def provider_status(admin: User = Depends(require_admin)) -> Dict[str, Any]:
    return {
        "providers": registry.health_report(),
        "configured_chains": {
            capability: settings.providers_for(capability)
            for capability in ("quote", "history", "option_chain", "news", "ipo")
        },
        "note": "No credential values are returned by this endpoint.",
    }


@router.get("/jobs")
def job_runs(limit: int = Query(default=100, le=500),
             job: Optional[str] = None,
             admin: User = Depends(require_admin),
             db: Session = Depends(db_session)) -> Dict[str, Any]:
    stmt = select(JobRunLog).order_by(JobRunLog.started_at.desc()).limit(limit)
    if job:
        stmt = stmt.where(JobRunLog.job_name == job)
    rows = db.execute(stmt).scalars().all()
    return {
        "runs": [
            {"id": r.id, "job": r.job_name, "status": r.status,
             "started_at": r.started_at.isoformat(),
             "finished_at": r.finished_at.isoformat() if r.finished_at else None,
             "duration_ms": r.duration_ms, "provider": r.provider,
             "records_received": r.records_received,
             "records_saved": r.records_saved,
             "records_rejected": r.records_rejected, "error": r.error}
            for r in rows
        ]
    }


@router.post("/jobs/{job_name}/run")
def run_job_now(job_name: str, admin: User = Depends(require_admin),
                db: Session = Depends(db_session)) -> Dict[str, Any]:
    from app.jobs import tasks

    runner = tasks.JOB_REGISTRY.get(job_name)
    if runner is None:
        raise HTTPException(
            404,
            f"Unknown job. Available: {', '.join(sorted(tasks.JOB_REGISTRY))}",
        )
    audit.record(db, action="JOB_TRIGGERED", entity_type="job",
                 entity_id=job_name, actor_id=admin.id,
                 actor_email=admin.email)
    db.commit()
    result = runner()
    return {"job": job_name, "result": result}


@router.get("/audit")
def audit_log(entity_type: Optional[str] = None,
              entity_id: Optional[str] = None,
              action: Optional[str] = None,
              limit: int = Query(default=200, le=1000),
              admin: User = Depends(require_admin),
              db: Session = Depends(db_session)) -> Dict[str, Any]:
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if entity_id:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    rows = db.execute(stmt).scalars().all()
    return {
        "entries": [
            {
                "id": r.id, "action": r.action, "entity_type": r.entity_type,
                "entity_id": r.entity_id, "actor_email": r.actor_email,
                "actor_role": r.actor_role, "reason": r.reason,
                "old_value": json.loads(r.old_value) if r.old_value else None,
                "new_value": json.loads(r.new_value) if r.new_value else None,
                "ip_address": r.ip_address,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ],
        "note": "The audit log is append-only. This API offers no way to edit "
                "or delete an entry.",
    }
