"""IPO endpoints: list, detail, research, GMP history, simulator."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.filters import hide_demo
from app.api.deps import db_session, rate_limit
from app.core.compliance import disclaimers
from app.models.ipo import (Ipo, IpoAnalysis, IpoFinancials, IpoGmpHistory,
                            IpoRiskFactor, IpoSubscription)
from app.models.research import ResearchDocument
from app.services.ipo_analysis import ipo_analysis_service

router = APIRouter(prefix="/ipo", tags=["ipo"])


def _ipo_or_404(db: Session, identifier: str) -> Ipo:
    ipo = db.execute(
        hide_demo(
            select(Ipo).where((Ipo.id == identifier) | (Ipo.slug == identifier)),
            Ipo,
        )
    ).scalars().first()
    if ipo is None:
        raise HTTPException(404, f"No IPO found for '{identifier}'.")
    return ipo


def _latest_gmp(db: Session, ipo_id: str) -> Optional[IpoGmpHistory]:
    return db.execute(
        select(IpoGmpHistory).where(IpoGmpHistory.ipo_id == ipo_id)
        .order_by(IpoGmpHistory.observed_on.desc()).limit(1)
    ).scalars().first()


def _latest_subscription(db: Session, ipo_id: str) -> Optional[IpoSubscription]:
    return db.execute(
        select(IpoSubscription).where(IpoSubscription.ipo_id == ipo_id)
        .order_by(IpoSubscription.observed_at.desc()).limit(1)
    ).scalars().first()


@router.get("", dependencies=[Depends(rate_limit("ipo", 120))])
def list_ipos(status: Optional[str] = None,
              ipo_type: Optional[str] = None,
              db: Session = Depends(db_session)) -> Dict[str, Any]:
    stmt = hide_demo(select(Ipo), Ipo).order_by(
        Ipo.open_date.desc().nullslast())
    if status:
        stmt = stmt.where(Ipo.status == status.upper())
    if ipo_type:
        stmt = stmt.where(Ipo.ipo_type == ipo_type.upper())

    rows = []
    for ipo in db.execute(stmt).scalars().all():
        gmp = _latest_gmp(db, ipo.id)
        subscription = _latest_subscription(db, ipo.id)
        analysis = db.execute(
            select(IpoAnalysis).where(IpoAnalysis.ipo_id == ipo.id)
            .order_by(IpoAnalysis.version.desc()).limit(1)
        ).scalars().first()
        rows.append({
            "id": ipo.id, "slug": ipo.slug, "company_name": ipo.company_name,
            "symbol": ipo.symbol, "status": ipo.status, "type": ipo.ipo_type,
            "open_date": ipo.open_date.isoformat() if ipo.open_date else None,
            "close_date": ipo.close_date.isoformat() if ipo.close_date else None,
            "listing_date": ipo.listing_date.isoformat()
            if ipo.listing_date else None,
            "price_band": [ipo.price_band_low, ipo.price_band_high],
            "lot_size": ipo.lot_size,
            "retail_min_investment": ipo.retail_min_investment,
            "issue_size_cr": ipo.issue_size_cr,
            "industry": ipo.industry,
            "gmp": gmp.gmp if gmp else None,
            "gmp_pct": gmp.gmp_pct if gmp else None,
            "gmp_observed_on": gmp.observed_on.isoformat() if gmp else None,
            "gmp_source": gmp.source_name if gmp else None,
            "estimated_listing_price": gmp.estimated_listing_price if gmp else None,
            "subscription_total": subscription.total_times if subscription else None,
            "research_score": analysis.overall_research_score if analysis else None,
            "research_label": analysis.label if analysis else None,
            "listing_price": ipo.listing_price,
            "listing_gain_pct": ipo.listing_gain_pct,
            "is_demo": ipo.is_demo,
        })

    return {
        "count": len(rows), "rows": rows,
        "gmp_disclaimer": disclaimers()["gmp"],
    }


@router.get("/{identifier}")
def ipo_detail(identifier: str,
               db: Session = Depends(db_session)) -> Dict[str, Any]:
    ipo = _ipo_or_404(db, identifier)
    gmp = _latest_gmp(db, ipo.id)
    subscription = _latest_subscription(db, ipo.id)

    financials = db.execute(
        select(IpoFinancials).where(IpoFinancials.ipo_id == ipo.id)
        .order_by(IpoFinancials.period_end)
    ).scalars().all()
    risks = db.execute(
        select(IpoRiskFactor).where(IpoRiskFactor.ipo_id == ipo.id)
    ).scalars().all()
    documents = db.execute(
        select(ResearchDocument).where(ResearchDocument.ipo_id == ipo.id)
        .order_by(ResearchDocument.document_date.desc())
    ).scalars().all()

    return {
        "ipo": {
            "id": ipo.id, "slug": ipo.slug, "company_name": ipo.company_name,
            "symbol": ipo.symbol, "status": ipo.status, "type": ipo.ipo_type,
            "open_date": ipo.open_date.isoformat() if ipo.open_date else None,
            "close_date": ipo.close_date.isoformat() if ipo.close_date else None,
            "allotment_date": ipo.allotment_date.isoformat()
            if ipo.allotment_date else None,
            "listing_date": ipo.listing_date.isoformat()
            if ipo.listing_date else None,
            "price_band_low": ipo.price_band_low,
            "price_band_high": ipo.price_band_high,
            "face_value": ipo.face_value, "lot_size": ipo.lot_size,
            "retail_min_investment": ipo.retail_min_investment,
            "issue_size_cr": ipo.issue_size_cr,
            "fresh_issue_cr": ipo.fresh_issue_cr, "ofs_cr": ipo.ofs_cr,
            "promoter_selling_note": ipo.promoter_selling_note,
            "use_of_proceeds": _json(ipo.use_of_proceeds),
            "lead_managers": _json(ipo.lead_managers),
            "registrar": ipo.registrar,
            "listing_exchanges": ipo.listing_exchanges,
            "industry": ipo.industry,
            "anchor_investment_cr": ipo.anchor_investment_cr,
            "anchor_investors": _json(ipo.anchor_investors),
            "listing_price": ipo.listing_price,
            "listing_gain_pct": ipo.listing_gain_pct,
            "source": ipo.source_name, "source_url": ipo.source_url,
            "data_status": ipo.data_status, "is_demo": ipo.is_demo,
        },
        "gmp": {
            "current": gmp.gmp if gmp else None,
            "current_pct": gmp.gmp_pct if gmp else None,
            "estimated_listing_price": gmp.estimated_listing_price if gmp else None,
            "observed_on": gmp.observed_on.isoformat() if gmp else None,
            "source": gmp.source_name if gmp else None,
            "confidence_note": gmp.confidence_note if gmp else None,
            "available": gmp is not None,
            "disclaimer": disclaimers()["gmp"],
        },
        "subscription": {
            "available": subscription is not None,
            "qib": subscription.qib_times if subscription else None,
            "nii": subscription.nii_times if subscription else None,
            "retail": subscription.retail_times if subscription else None,
            "employee": subscription.employee_times if subscription else None,
            "total": subscription.total_times if subscription else None,
            "day": subscription.day_number if subscription else None,
            "observed_at": subscription.observed_at.isoformat()
            if subscription else None,
            "source": subscription.source_name if subscription else None,
        },
        "financials": [
            {
                "period_label": f.period_label,
                "period_end": f.period_end.isoformat() if f.period_end else None,
                "revenue": f.revenue, "ebitda": f.ebitda,
                "ebitda_margin": f.ebitda_margin, "pat": f.pat,
                "net_margin": f.net_margin, "eps": f.eps,
                "net_worth": f.net_worth, "total_debt": f.total_debt,
                "cash": f.cash, "working_capital": f.working_capital,
                "roe": f.roe, "roce": f.roce,
                "source": f.source_name, "is_demo": f.is_demo,
            }
            for f in financials
        ],
        "risk_factors": [
            {"category": r.category, "description": r.description,
             "severity": r.severity, "quantum": r.quantum,
             "quantum_unit": r.quantum_unit, "source": r.source_name,
             "is_demo": r.is_demo}
            for r in risks
        ],
        "documents": [
            {"type": d.doc_type, "title": d.title, "url": d.url,
             "date": d.document_date.isoformat() if d.document_date else None,
             "extraction_status": d.extraction_status,
             "source": d.source_name}
            for d in documents
        ],
    }


@router.get("/{identifier}/gmp")
def ipo_gmp_history(identifier: str,
                    db: Session = Depends(db_session)) -> Dict[str, Any]:
    ipo = _ipo_or_404(db, identifier)
    rows = db.execute(
        select(IpoGmpHistory).where(IpoGmpHistory.ipo_id == ipo.id)
        .order_by(IpoGmpHistory.observed_on)
    ).scalars().all()

    subscriptions = db.execute(
        select(IpoSubscription).where(IpoSubscription.ipo_id == ipo.id)
        .order_by(IpoSubscription.observed_at)
    ).scalars().all()

    return {
        "ipo_id": ipo.id, "company_name": ipo.company_name,
        "price_band": [ipo.price_band_low, ipo.price_band_high],
        "series": [
            {
                "observed_on": r.observed_on.isoformat(),
                "gmp": r.gmp, "gmp_pct": r.gmp_pct,
                "estimated_listing_price": r.estimated_listing_price,
                "reference_price": r.reference_price,
                "kostak": r.kostak, "subject_to_sauda": r.subject_to_sauda,
                "source": r.source_name, "provider": r.provider,
                "data_status": r.data_status, "is_demo": r.is_demo,
            }
            for r in rows
        ],
        "subscription_series": [
            {
                "observed_at": s.observed_at.isoformat(),
                "day": s.day_number, "qib": s.qib_times, "nii": s.nii_times,
                "retail": s.retail_times, "total": s.total_times,
                "source": s.source_name,
            }
            for s in subscriptions
        ],
        "available": bool(rows),
        "disclaimer": disclaimers()["gmp"],
        "reading_note": (
            "The trend matters more than the level. A premium that is falling "
            "through the subscription window has historically been a different "
            "signal from one that is rising, but neither determines the "
            "listing price."
        ),
    }


@router.get("/{identifier}/research")
def ipo_research(identifier: str,
                 db: Session = Depends(db_session)) -> Dict[str, Any]:
    ipo = _ipo_or_404(db, identifier)

    financials = [
        {
            "period_label": f.period_label,
            "period_end": f.period_end, "revenue": f.revenue,
            "ebitda": f.ebitda, "ebitda_margin": f.ebitda_margin,
            "pat": f.pat, "net_margin": f.net_margin, "eps": f.eps,
            "net_worth": f.net_worth, "total_debt": f.total_debt,
            "cash": f.cash, "roe": f.roe, "roce": f.roce,
        }
        for f in db.execute(
            select(IpoFinancials).where(IpoFinancials.ipo_id == ipo.id)
            .order_by(IpoFinancials.period_end)
        ).scalars().all()
    ]
    risks = [
        {"category": r.category, "description": r.description,
         "severity": r.severity, "quantum": r.quantum,
         "quantum_unit": r.quantum_unit}
        for r in db.execute(
            select(IpoRiskFactor).where(IpoRiskFactor.ipo_id == ipo.id)
        ).scalars().all()
    ]

    gmp = _latest_gmp(db, ipo.id)
    gmp_history = [
        {"gmp": g.gmp, "gmp_pct": g.gmp_pct,
         "observed_on": g.observed_on.isoformat()}
        for g in db.execute(
            select(IpoGmpHistory).where(IpoGmpHistory.ipo_id == ipo.id)
            .order_by(IpoGmpHistory.observed_on)
        ).scalars().all()
    ]
    subscription_row = _latest_subscription(db, ipo.id)

    stored = db.execute(
        select(IpoAnalysis).where(IpoAnalysis.ipo_id == ipo.id)
        .order_by(IpoAnalysis.version.desc()).limit(1)
    ).scalars().first()
    peers = _json(stored.peer_comparison_json) if stored else None

    assessment = ipo_analysis_service.assess(
        ipo={
            "industry": ipo.industry, "price_band_low": ipo.price_band_low,
            "price_band_high": ipo.price_band_high,
            "issue_size_cr": ipo.issue_size_cr,
            "fresh_issue_cr": ipo.fresh_issue_cr, "ofs_cr": ipo.ofs_cr,
            "use_of_proceeds": _json(ipo.use_of_proceeds),
        },
        financials=financials,
        risk_factors=risks,
        latest_gmp={
            "gmp": gmp.gmp, "gmp_pct": gmp.gmp_pct,
            "observed_on": gmp.observed_on.isoformat(),
        } if gmp else None,
        gmp_history=gmp_history,
        subscription={
            "qib_times": subscription_row.qib_times,
            "nii_times": subscription_row.nii_times,
            "retail_times": subscription_row.retail_times,
            "total_times": subscription_row.total_times,
        } if subscription_row else None,
        peers=peers,
    )

    return {
        "ipo_id": ipo.id,
        "company_name": ipo.company_name,
        "assessment": assessment.to_dict(),
        "stored_version": stored.version if stored else None,
        "is_demo": ipo.is_demo,
    }


class IpoSimulationRequest(BaseModel):
    lots: int = Field(ge=1, le=1000)
    capital: float = Field(gt=0)
    gmp: Optional[float] = None
    price: Optional[float] = None


@router.post("/{identifier}/simulate")
def ipo_simulate(identifier: str, payload: IpoSimulationRequest,
                 db: Session = Depends(db_session)) -> Dict[str, Any]:
    ipo = _ipo_or_404(db, identifier)
    if not ipo.lot_size or not (ipo.price_band_high or payload.price):
        raise HTTPException(
            400,
            "This IPO has no lot size or price band recorded, so an "
            "application cannot be simulated.",
        )
    gmp = payload.gmp
    if gmp is None:
        latest = _latest_gmp(db, ipo.id)
        gmp = latest.gmp if latest else None

    result = ipo_analysis_service.simulate_application(
        capital=payload.capital, lots=payload.lots, lot_size=ipo.lot_size,
        price=payload.price or ipo.price_band_high, gmp=gmp,
    )
    return {"ipo_id": ipo.id, "company_name": ipo.company_name, **result}


def _json(raw: Optional[str]):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw
