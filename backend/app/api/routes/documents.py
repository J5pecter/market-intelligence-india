"""Company document research: register, extract, review, cite.

The review gate is the important part of this module. `POST .../extract` never
writes a fact — it writes citations with `review_status=PENDING`. Only
`POST .../citations/{id}/approve` promotes a figure into the fundamentals
tables, and that always names the approver in the audit log.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import (APIRouter, Depends, File, Form, HTTPException, Query,
                     Request, Response, UploadFile)
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import (client_ip, db_session, rate_limit, require_admin,
                          require_analyst)
from app.core.config import settings
from app.models.ipo import Ipo
from app.models.research import ResearchCitation, ResearchDocument
from app.models.user import User
from app.services import audit
from app.services.documents import (AUTO_ACCEPT_CONFIDENCE, ExtractionError,
                                    document_pipeline)

router = APIRouter(tags=["documents"])

DOC_TYPES = [
    "ANNUAL_REPORT", "QUARTERLY_RESULT", "INVESTOR_PRESENTATION",
    "EARNINGS_RELEASE", "TRANSCRIPT", "EXCHANGE_FILING", "ANNOUNCEMENT",
    "DRHP", "RHP", "OFFER_DOCUMENT", "CREDIT_RATING", "SHAREHOLDING",
]

# Uploaded files live outside the source tree and are never served statically.
UPLOAD_ROOT = Path(__file__).resolve().parents[3] / "storage" / "documents"
ALLOWED_SUFFIXES = {".pdf", ".html", ".htm", ".txt", ".md"}
MAX_UPLOAD_BYTES = 80 * 1024 * 1024


# --------------------------------------------------------------------------
# Read
# --------------------------------------------------------------------------


@router.get("/documents", dependencies=[Depends(rate_limit("documents", 120))])
def list_documents(
    symbol: Optional[str] = None,
    ipo_id: Optional[str] = None,
    doc_type: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(default=100, le=300),
    db: Session = Depends(db_session),
) -> Dict[str, Any]:
    stmt = (
        select(ResearchDocument)
        .order_by(ResearchDocument.document_date.desc().nullslast(),
                  ResearchDocument.created_at.desc())
        .limit(limit)
    )
    if symbol:
        stmt = stmt.where(ResearchDocument.symbol == symbol.upper())
    if ipo_id:
        stmt = stmt.where(ResearchDocument.ipo_id == ipo_id)
    if doc_type:
        stmt = stmt.where(ResearchDocument.doc_type == doc_type.upper())
    if status:
        stmt = stmt.where(ResearchDocument.extraction_status == status.upper())

    rows = db.execute(stmt).scalars().all()

    counts = dict(db.execute(
        select(ResearchCitation.document_id, func.count(ResearchCitation.id))
        .where(ResearchCitation.document_id.in_([r.id for r in rows] or [""]))
        .group_by(ResearchCitation.document_id)
    ).all())
    pending = dict(db.execute(
        select(ResearchCitation.document_id, func.count(ResearchCitation.id))
        .where(ResearchCitation.document_id.in_([r.id for r in rows] or [""]))
        .where(ResearchCitation.review_status == "PENDING")
        .group_by(ResearchCitation.document_id)
    ).all())

    return {
        "document_types": DOC_TYPES,
        "count": len(rows),
        "documents": [
            {
                "id": row.id,
                "symbol": row.symbol,
                "ipo_id": row.ipo_id,
                "doc_type": row.doc_type,
                "title": row.title,
                "document_date": row.document_date.isoformat()
                if row.document_date else None,
                "url": row.url,
                "has_local_copy": bool(row.local_path),
                "page_count": row.page_count,
                "extraction_status": row.extraction_status,
                "extraction_note": _json_or_text(row.extraction_note),
                "source": row.source_name,
                "citations": counts.get(row.id, 0),
                "pending_review": pending.get(row.id, 0),
                "is_demo": row.is_demo,
            }
            for row in rows
        ],
        "review_note": (
            "Machine-extracted figures are citations awaiting review. They do "
            "not appear in any research page until an operator approves them."
        ),
    }


@router.get("/documents/{document_id}")
def document_detail(document_id: str,
                    db: Session = Depends(db_session)) -> Dict[str, Any]:
    document = _document_or_404(db, document_id)
    citations = db.execute(
        select(ResearchCitation)
        .where(ResearchCitation.document_id == document.id)
        .order_by(ResearchCitation.citation_type,
                  ResearchCitation.confidence.desc())
    ).scalars().all()

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for citation in citations:
        grouped.setdefault(citation.citation_type, []).append(
            _citation_payload(citation, document)
        )

    return {
        "document": {
            "id": document.id,
            "symbol": document.symbol,
            "ipo_id": document.ipo_id,
            "doc_type": document.doc_type,
            "title": document.title,
            "document_date": document.document_date.isoformat()
            if document.document_date else None,
            "url": document.url,
            "page_count": document.page_count,
            "extraction_status": document.extraction_status,
            "extraction_note": _json_or_text(document.extraction_note),
            "source": document.source_name,
            "data_status": document.data_status,
            "is_demo": document.is_demo,
        },
        "citations": grouped,
        "summary": {
            "total": len(citations),
            "pending": sum(1 for c in citations if c.review_status == "PENDING"),
            "approved": sum(1 for c in citations if c.review_status == "APPROVED"),
            "rejected": sum(1 for c in citations if c.review_status == "REJECTED"),
            "high_confidence": sum(
                1 for c in citations
                if (c.confidence or 0) >= AUTO_ACCEPT_CONFIDENCE
            ),
            "auto_accept_threshold": AUTO_ACCEPT_CONFIDENCE,
        },
    }


@router.get("/documents/citations/queue")
def review_queue(
    symbol: Optional[str] = None,
    citation_type: Optional[str] = None,
    min_confidence: float = Query(default=0.0, ge=0, le=1),
    limit: int = Query(default=100, le=400),
    db: Session = Depends(db_session),
) -> Dict[str, Any]:
    """Everything awaiting a human decision, newest and most confident first."""
    stmt = (
        select(ResearchCitation, ResearchDocument)
        .join(ResearchDocument,
              ResearchDocument.id == ResearchCitation.document_id)
        .where(ResearchCitation.review_status == "PENDING")
        .where(ResearchCitation.extracted_by == "PIPELINE")
        .order_by(ResearchCitation.confidence.desc())
        .limit(limit)
    )
    if symbol:
        stmt = stmt.where(ResearchDocument.symbol == symbol.upper())
    if citation_type:
        stmt = stmt.where(ResearchCitation.citation_type == citation_type.upper())
    if min_confidence:
        stmt = stmt.where(ResearchCitation.confidence >= min_confidence)

    rows = db.execute(stmt).all()
    return {
        "count": len(rows),
        "auto_accept_threshold": AUTO_ACCEPT_CONFIDENCE,
        "citations": [
            _citation_payload(citation, document)
            for citation, document in rows
        ],
        "guidance": (
            "Confidence measures how well the line matched an expected "
            "statement row - not whether the figure is correct. Check the "
            "quote against the cited page before approving."
        ),
    }


@router.get("/stocks/{symbol}/documents")
def documents_for_symbol(symbol: str,
                         db: Session = Depends(db_session)) -> Dict[str, Any]:
    """Approved, cited claims for one company - what the research pages use."""
    upper = symbol.upper()
    rows = db.execute(
        select(ResearchCitation, ResearchDocument)
        .join(ResearchDocument,
              ResearchDocument.id == ResearchCitation.document_id)
        .where(ResearchDocument.symbol == upper)
        .order_by(ResearchDocument.document_date.desc().nullslast(),
                  ResearchCitation.confidence.desc())
    ).all()

    approved = [(c, d) for c, d in rows if c.review_status == "APPROVED"]
    pending = [(c, d) for c, d in rows if c.review_status == "PENDING"]

    return {
        "symbol": upper,
        "documents": [
            {
                "id": document.id, "doc_type": document.doc_type,
                "title": document.title, "url": document.url,
                "document_date": document.document_date.isoformat()
                if document.document_date else None,
                "extraction_status": document.extraction_status,
            }
            for document in {d.id: d for _, d in rows}.values()
        ],
        "approved_claims": [_citation_payload(c, d) for c, d in approved],
        "pending_claims": len(pending),
        "note": (
            "Only approved claims are shown as findings. "
            f"{len(pending)} extracted claim(s) are still awaiting review and "
            f"are deliberately excluded."
        ) if pending else "All extracted claims for this company have been reviewed.",
    }


# --------------------------------------------------------------------------
# Write
# --------------------------------------------------------------------------


class DocumentCreate(BaseModel):
    doc_type: str
    title: str = ""
    symbol: Optional[str] = None
    ipo_id: Optional[str] = None
    url: Optional[str] = None
    document_date: Optional[date] = None
    source_name: str = "Operator entry"


@router.post("/admin/documents", status_code=201)
def register_document(payload: DocumentCreate, request: Request,
                      analyst: User = Depends(require_analyst),
                      db: Session = Depends(db_session)) -> Dict[str, Any]:
    _validate_target(db, payload.symbol, payload.ipo_id)
    if payload.doc_type.upper() not in DOC_TYPES:
        raise HTTPException(400, f"doc_type must be one of {DOC_TYPES}")
    if not payload.url:
        raise HTTPException(
            400, "Provide a URL, or use the upload endpoint for a local file."
        )

    document = ResearchDocument(
        symbol=payload.symbol.upper() if payload.symbol else None,
        ipo_id=payload.ipo_id,
        doc_type=payload.doc_type.upper(),
        title=payload.title or payload.doc_type.replace("_", " ").title(),
        document_date=payload.document_date,
        url=payload.url,
        extraction_status="NOT_STARTED",
        provider="manual",
        source_name=payload.source_name,
        source_url=payload.url,
        data_status="MANUAL",
        observed_at=datetime.now(tz=timezone.utc),
    )
    db.add(document)
    db.flush()

    audit.record(db, action="DOCUMENT_REGISTERED",
                 entity_type="research_document", entity_id=document.id,
                 actor_id=analyst.id, actor_email=analyst.email,
                 new_value=payload.model_dump(mode="json"),
                 ip_address=client_ip(request))
    db.commit()
    return {"id": document.id, "extraction_status": document.extraction_status}


@router.post("/admin/documents/upload", status_code=201)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    doc_type: str = Form(...),
    title: str = Form(default=""),
    symbol: Optional[str] = Form(default=None),
    ipo_id: Optional[str] = Form(default=None),
    document_date: Optional[str] = Form(default=None),
    source_name: str = Form(default="Operator upload"),
    analyst: User = Depends(require_analyst),
    db: Session = Depends(db_session),
) -> Dict[str, Any]:
    if doc_type.upper() not in DOC_TYPES:
        raise HTTPException(400, f"doc_type must be one of {DOC_TYPES}")
    _validate_target(db, symbol, ipo_id)

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(
            400,
            f"Unsupported file type '{suffix}'. Allowed: "
            f"{sorted(ALLOWED_SUFFIXES)}.",
        )

    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            413, f"File is {len(data) / 1e6:.0f} MB, above the "
                 f"{MAX_UPLOAD_BYTES / 1e6:.0f} MB limit."
        )
    if not data:
        raise HTTPException(400, "The uploaded file is empty.")

    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(file.filename or "doc").name)
    stored = UPLOAD_ROOT / f"{datetime.now(tz=timezone.utc):%Y%m%d%H%M%S}_{safe_name}"
    stored.write_bytes(data)

    parsed_date = None
    if document_date:
        try:
            parsed_date = date.fromisoformat(document_date)
        except ValueError as exc:
            raise HTTPException(400, "document_date must be an ISO date.") from exc

    document = ResearchDocument(
        symbol=symbol.upper() if symbol else None,
        ipo_id=ipo_id,
        doc_type=doc_type.upper(),
        title=title or safe_name,
        document_date=parsed_date,
        local_path=str(stored),
        extraction_status="NOT_STARTED",
        provider="manual",
        source_name=source_name,
        data_status="MANUAL",
        observed_at=datetime.now(tz=timezone.utc),
    )
    db.add(document)
    db.flush()

    audit.record(db, action="DOCUMENT_UPLOADED",
                 entity_type="research_document", entity_id=document.id,
                 actor_id=analyst.id, actor_email=analyst.email,
                 new_value={"filename": safe_name, "bytes": len(data),
                            "doc_type": doc_type.upper()},
                 ip_address=client_ip(request))
    db.commit()
    return {"id": document.id, "stored_bytes": len(data),
            "extraction_status": document.extraction_status}


@router.post("/admin/documents/{document_id}/extract")
def extract_document(document_id: str,
                     analyst: User = Depends(require_analyst),
                     db: Session = Depends(db_session)) -> Dict[str, Any]:
    """Read the document and queue everything it found for review."""
    document = _document_or_404(db, document_id)
    try:
        summary = document_pipeline.run(db, document, actor=analyst)
    except ExtractionError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return summary.to_dict()


class CitationDecision(BaseModel):
    override_value: Optional[float] = None
    override_period: Optional[str] = None
    reason: str = Field(default="", max_length=600)


@router.post("/admin/documents/citations/{citation_id}/approve")
def approve_citation(citation_id: str, payload: CitationDecision,
                     analyst: User = Depends(require_analyst),
                     db: Session = Depends(db_session)) -> Dict[str, Any]:
    citation = _citation_or_404(db, citation_id)
    if citation.review_status == "APPROVED":
        raise HTTPException(400, "That citation is already approved.")
    result = document_pipeline.approve(
        db, citation, actor=analyst,
        override_value=payload.override_value,
        override_period=payload.override_period,
    )
    db.commit()
    return {
        **result,
        "review_status": citation.review_status,
        "note": (
            f"Promoted to {result['promoted_to']}."
            if result.get("promoted_to") else
            "Approved as a cited claim. It was not promoted to a fundamentals "
            "table - either it is commentary, or it has no period label."
        ),
    }


@router.post("/admin/documents/citations/{citation_id}/reject")
def reject_citation(citation_id: str, payload: CitationDecision,
                    analyst: User = Depends(require_analyst),
                    db: Session = Depends(db_session)) -> Dict[str, Any]:
    citation = _citation_or_404(db, citation_id)
    document_pipeline.reject(db, citation, actor=analyst, reason=payload.reason)
    db.commit()
    return {"citation_id": citation.id, "review_status": citation.review_status}


@router.delete("/admin/documents/{document_id}", status_code=204,
               response_class=Response, response_model=None)
def delete_document(document_id: str, request: Request,
                    admin: User = Depends(require_admin),
                    db: Session = Depends(db_session)) -> None:
    document = _document_or_404(db, document_id)
    audit.record(db, action="DOCUMENT_DELETED",
                 entity_type="research_document", entity_id=document.id,
                 actor_id=admin.id, actor_email=admin.email,
                 old_value={"title": document.title,
                            "doc_type": document.doc_type},
                 ip_address=client_ip(request))
    # Citations cascade; approved promotions are left in place deliberately,
    # because they are already part of the audited record.
    db.delete(document)
    db.commit()


# --------------------------------------------------------------------------


def _document_or_404(db: Session, document_id: str) -> ResearchDocument:
    document = db.execute(
        select(ResearchDocument).where(ResearchDocument.id == document_id)
    ).scalars().first()
    if document is None:
        raise HTTPException(404, "Document not found.")
    return document


def _citation_or_404(db: Session, citation_id: str) -> ResearchCitation:
    citation = db.execute(
        select(ResearchCitation).where(ResearchCitation.id == citation_id)
    ).scalars().first()
    if citation is None:
        raise HTTPException(404, "Citation not found.")
    return citation


def _validate_target(db: Session, symbol: Optional[str],
                     ipo_id: Optional[str]) -> None:
    if not symbol and not ipo_id:
        raise HTTPException(
            400, "A document must be attached to either a symbol or an IPO."
        )
    if ipo_id:
        exists = db.execute(select(Ipo).where(Ipo.id == ipo_id)).scalars().first()
        if exists is None:
            raise HTTPException(404, "That IPO does not exist.")


def _citation_payload(citation: ResearchCitation,
                      document: ResearchDocument) -> Dict[str, Any]:
    return {
        "id": citation.id,
        "type": citation.citation_type,
        "claim": citation.claim,
        "metric_key": citation.metric_key,
        "raw_value": citation.raw_value,
        "normalised_value": citation.normalised_value,
        "value": citation.metric_value,
        "unit": citation.unit,
        "unit_multiplier": citation.unit_multiplier,
        "period_label": citation.period_label,
        "quote": citation.quote,
        "page": citation.page_reference,
        "section": citation.section,
        "confidence": citation.confidence,
        "confidence_reasons": _json_list(citation.confidence_reasons),
        "extracted_by": citation.extracted_by,
        "review_status": citation.review_status,
        "reviewed_by": citation.reviewed_by,
        "reviewed_at": citation.reviewed_at.isoformat()
        if citation.reviewed_at else None,
        "needs_review": citation.review_status == "PENDING",
        "source": {
            "document_id": document.id,
            "doc_type": document.doc_type,
            "title": document.title,
            "url": document.url,
            "document_date": document.document_date.isoformat()
            if document.document_date else None,
            "symbol": document.symbol,
            "ipo_id": document.ipo_id,
        },
    }


def _json_list(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else [str(parsed)]
    except (json.JSONDecodeError, TypeError):
        return [raw]


def _json_or_text(raw: Optional[str]) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw
