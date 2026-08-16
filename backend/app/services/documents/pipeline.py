"""Document extraction orchestrator.

Takes a stored `ResearchDocument`, reads it, extracts what it can, and writes
**citations** - never facts. Every extracted figure lands in a review queue
with its page, its quote, its confidence and the reasons behind that
confidence. An operator approves it before it reaches `financial_statements`
or `ipo_financials`.

That gate is the whole point. An extractor that writes straight into the
fundamentals tables would let a misparsed unit become a P/E ratio on a research
page, with nothing on screen to say a machine guessed it.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ipo import IpoFinancials, IpoRiskFactor
from app.models.research import ResearchCitation, ResearchDocument
from app.services import audit
from app.services.documents import figures as fig
from app.services.documents import sectioning, text_extraction
from app.services.documents.text_extraction import (ExtractedDocument,
                                                    ExtractionError)

logger = logging.getLogger(__name__)

# Above this, a figure is still queued but pre-marked as safe to accept.
AUTO_ACCEPT_CONFIDENCE = 0.75

# Documents larger than this are refused rather than silently truncated.
MAX_DOCUMENT_BYTES = 80 * 1024 * 1024


@dataclass
class ExtractionSummary:
    document_id: str
    status: str
    method: str
    page_count: int
    empty_pages: int
    figures_found: int
    figures_high_confidence: int
    commentary_found: int
    risk_factors_found: int
    sections: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    citations_written: int = 0
    duration_ms: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "document_id": self.document_id,
            "status": self.status,
            "method": self.method,
            "page_count": self.page_count,
            "empty_pages": self.empty_pages,
            "figures_found": self.figures_found,
            "figures_high_confidence": self.figures_high_confidence,
            "commentary_found": self.commentary_found,
            "risk_factors_found": self.risk_factors_found,
            "sections": self.sections,
            "warnings": self.warnings,
            "citations_written": self.citations_written,
            "duration_ms": self.duration_ms,
            "review_note": (
                "Nothing here has entered the fundamentals tables. Every "
                "figure is a citation awaiting review, and each carries the "
                "page, the quote and the reason for its confidence score."
            ),
        }


class DocumentPipeline:

    # -- reading -----------------------------------------------------------

    def load(self, document: ResearchDocument) -> ExtractedDocument:
        """Read the document from disk or fetch it from its URL."""
        if document.local_path:
            path = Path(document.local_path)
            if path.exists():
                if path.stat().st_size > MAX_DOCUMENT_BYTES:
                    raise ExtractionError(
                        f"document is {path.stat().st_size / 1e6:.0f} MB, above "
                        f"the {MAX_DOCUMENT_BYTES / 1e6:.0f} MB limit"
                    )
                return text_extraction.extract_from_path(path)
            logger.warning("local_path %s is missing, falling back to the URL",
                           document.local_path)

        if not document.url:
            raise ExtractionError(
                "the document has neither a stored file nor a URL to fetch"
            )

        try:
            response = requests.get(
                document.url, timeout=60,
                headers={"User-Agent": "MarketIntelligenceIndia/1.0 "
                                       "(document research)"},
                stream=True,
            )
        except requests.RequestException as exc:
            raise ExtractionError(f"could not fetch the document: {exc}") from exc

        if response.status_code >= 400:
            raise ExtractionError(
                f"the source returned HTTP {response.status_code} for this "
                f"document"
            )

        declared = int(response.headers.get("content-length") or 0)
        if declared > MAX_DOCUMENT_BYTES:
            raise ExtractionError(
                f"the document is {declared / 1e6:.0f} MB, above the limit"
            )

        data = b""
        for chunk in response.iter_content(chunk_size=1 << 16):
            data += chunk
            if len(data) > MAX_DOCUMENT_BYTES:
                raise ExtractionError("the document exceeded the size limit "
                                      "while downloading")
        return text_extraction.extract_from_bytes(data, document.url)

    # -- the run -----------------------------------------------------------

    def run(
        self,
        db: Session,
        document: ResearchDocument,
        actor: Optional[Any] = None,
        write_citations: bool = True,
    ) -> ExtractionSummary:
        started = datetime.now(tz=timezone.utc)

        document.extraction_status = "RUNNING"
        db.flush()

        try:
            extracted = self.load(document)
        except ExtractionError as exc:
            document.extraction_status = "FAILED"
            document.extraction_note = str(exc)
            db.flush()
            return ExtractionSummary(
                document_id=document.id, status="FAILED", method="none",
                page_count=0, empty_pages=0, figures_found=0,
                figures_high_confidence=0, commentary_found=0,
                risk_factors_found=0, warnings=[str(exc)],
            )

        page_map, spans = sectioning.map_sections(extracted)
        found = fig.agreement_bonus(fig.extract_figures(extracted, page_map))
        commentary = sectioning.extract_commentary(extracted, page_map)
        risks = sectioning.extract_risk_factors(extracted, page_map)
        proceeds = sectioning.extract_use_of_proceeds(extracted, page_map)

        warnings = list(extracted.warnings)
        if not found:
            warnings.append(
                "No financial figures were extracted. Either the document has "
                "no statements, or its tables did not survive text extraction."
            )
        if not page_map:
            warnings.append(
                "No section headings were recognised, so figures could not be "
                "credited for appearing inside a statements section."
            )

        written = 0
        if write_citations:
            written = self._write_citations(
                db, document, found, commentary, risks, proceeds
            )

        high_confidence = sum(1 for f in found
                              if f.confidence >= AUTO_ACCEPT_CONFIDENCE)

        document.page_count = extracted.page_count
        document.extraction_status = (
            "EXTRACTED" if found or commentary or risks else "NO_CONTENT"
        )
        document.extraction_note = json.dumps({
            "method": extracted.method,
            "checksum": extracted.checksum,
            "figures": len(found),
            "high_confidence": high_confidence,
            "commentary": len(commentary),
            "risk_factors": len(risks),
            "warnings": warnings[:10],
            "extracted_at": started.isoformat(),
        })
        db.flush()

        audit.record(
            db, action="DOCUMENT_EXTRACTED", entity_type="research_document",
            entity_id=document.id,
            actor_id=getattr(actor, "id", None),
            actor_email=getattr(actor, "email", None),
            new_value={"figures": len(found), "citations": written,
                       "status": document.extraction_status},
            reason="Automated extraction; all figures queued for review",
        )

        duration = (datetime.now(tz=timezone.utc) - started).total_seconds() * 1000
        return ExtractionSummary(
            document_id=document.id,
            status=document.extraction_status,
            method=extracted.method,
            page_count=extracted.page_count,
            empty_pages=len(extracted.empty_pages),
            figures_found=len(found),
            figures_high_confidence=high_confidence,
            commentary_found=len(commentary),
            risk_factors_found=len(risks),
            sections=[span.to_dict() for span in spans],
            warnings=warnings,
            citations_written=written,
            duration_ms=round(duration, 1),
        )

    # -- persistence -------------------------------------------------------

    def _write_citations(
        self, db: Session, document: ResearchDocument,
        found: List[fig.ExtractedFigure],
        commentary: List[sectioning.Quote],
        risks: List[Dict[str, Any]],
        proceeds: List[Dict[str, Any]],
    ) -> int:
        # Replace this document's machine extractions; hand-entered citations
        # are never touched.
        existing = db.execute(
            select(ResearchCitation)
            .where(ResearchCitation.document_id == document.id)
            .where(ResearchCitation.extracted_by == "PIPELINE")
        ).scalars().all()
        for row in existing:
            db.delete(row)
        db.flush()

        written = 0

        for figure in found:
            db.add(ResearchCitation(
                document_id=document.id,
                claim=(
                    f"{figure.metric_label} "
                    f"{('for ' + figure.period_label) if figure.period_label else ''}"
                    f" = {figure.raw_value:,.2f}"
                    f"{(' ' + figure.unit) if figure.unit else ''}"
                ).replace("  ", " ").strip(),
                metric_key=figure.metric_key,
                metric_value=figure.normalised_value if figure.normalised_value
                is not None else figure.raw_value,
                raw_value=figure.raw_value,
                normalised_value=figure.normalised_value,
                unit=figure.unit,
                unit_multiplier=figure.unit_multiplier,
                period_label=figure.period_label,
                page_reference=str(figure.page),
                section=figure.section,
                quote=figure.quote,
                extracted_by="PIPELINE",
                confidence=figure.confidence,
                confidence_reasons=json.dumps(figure.confidence_reasons),
                citation_type="FIGURE",
                review_status="PENDING",
            ))
            written += 1

        for quote in commentary:
            db.add(ResearchCitation(
                document_id=document.id,
                claim=f"{quote.category.title()} commentary",
                page_reference=str(quote.page),
                section=quote.section,
                quote=quote.text,
                extracted_by="PIPELINE",
                confidence=quote.confidence,
                confidence_reasons=json.dumps([
                    f"Matched the cue '{quote.matched_cue}'",
                    quote.note,
                ]),
                citation_type="COMMENTARY",
                review_status="PENDING",
            ))
            written += 1

        for risk in risks:
            db.add(ResearchCitation(
                document_id=document.id,
                claim=f"Risk factor: {risk['category'].replace('_', ' ').title()}",
                metric_key=risk["category"],
                metric_value=risk.get("quantum"),
                raw_value=risk.get("quantum"),
                unit=risk.get("quantum_unit"),
                page_reference=str(risk["page"]),
                section="RISK_FACTORS",
                quote=risk["quote"],
                extracted_by="PIPELINE",
                confidence=risk["confidence"],
                confidence_reasons=json.dumps([risk["severity_basis"]]),
                citation_type="RISK_FACTOR",
                review_status="PENDING",
            ))
            written += 1

        for item in proceeds:
            db.add(ResearchCitation(
                document_id=document.id,
                claim="Use of proceeds line item",
                metric_key="use_of_proceeds",
                metric_value=item.get("amount"),
                raw_value=item.get("amount"),
                page_reference=str(item["page"]),
                section="USE_OF_PROCEEDS",
                quote=item["purpose"],
                extracted_by="PIPELINE",
                confidence=0.6,
                confidence_reasons=json.dumps([
                    f"Matched the cue '{item['matched_cue']}'", item["note"],
                ]),
                citation_type="USE_OF_PROCEEDS",
                review_status="PENDING",
            ))
            written += 1

        db.flush()
        return written

    # -- review ------------------------------------------------------------

    def approve(
        self, db: Session, citation: ResearchCitation, actor: Optional[Any] = None,
        override_value: Optional[float] = None,
        override_period: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Promote a reviewed citation into the fundamentals tables.

        This is the only path by which an extracted number becomes a number the
        research pages will use, and it always leaves an audit entry naming who
        approved it.
        """
        before = {
            "review_status": citation.review_status,
            "metric_value": citation.metric_value,
            "period_label": citation.period_label,
        }

        if override_value is not None:
            citation.normalised_value = override_value
            citation.metric_value = override_value
            citation.confidence_reasons = json.dumps(
                json.loads(citation.confidence_reasons or "[]") +
                [f"Value corrected by reviewer to {override_value}"]
            )
        if override_period:
            citation.period_label = override_period

        citation.review_status = "APPROVED"
        citation.reviewed_by = getattr(actor, "email", None)
        citation.reviewed_at = datetime.now(tz=timezone.utc)

        promoted = self._promote(db, citation)

        audit.record_change(
            db, entity_type="research_citation", entity_id=citation.id,
            before=before,
            after={"review_status": "APPROVED",
                   "metric_value": citation.metric_value,
                   "period_label": citation.period_label},
            actor=actor, action="CITATION_APPROVED",
            reason=f"Approved from document {citation.document_id}"
                   + (f"; promoted to {promoted}" if promoted else ""),
        )
        db.flush()
        return {"citation_id": citation.id, "promoted_to": promoted}

    def reject(self, db: Session, citation: ResearchCitation,
               actor: Optional[Any] = None, reason: str = "") -> None:
        before = {"review_status": citation.review_status}
        citation.review_status = "REJECTED"
        citation.reviewed_by = getattr(actor, "email", None)
        citation.reviewed_at = datetime.now(tz=timezone.utc)
        audit.record_change(
            db, entity_type="research_citation", entity_id=citation.id,
            before=before, after={"review_status": "REJECTED"},
            actor=actor, action="CITATION_REJECTED",
            reason=reason or "Rejected on review",
        )
        db.flush()

    def _promote(self, db: Session,
                 citation: ResearchCitation) -> Optional[str]:
        """Write an approved figure into the table it belongs in."""
        document = db.execute(
            select(ResearchDocument)
            .where(ResearchDocument.id == citation.document_id)
        ).scalars().first()
        if document is None:
            return None

        if citation.citation_type == "RISK_FACTOR" and document.ipo_id:
            db.add(IpoRiskFactor(
                ipo_id=document.ipo_id,
                category=citation.metric_key or "OTHER",
                description=citation.quote or citation.claim,
                severity="HIGH" if (citation.raw_value or 0) >= 25 else "MEDIUM",
                quantum=citation.raw_value,
                quantum_unit=citation.unit,
                provider="pipeline",
                source_name=f"{document.title or document.doc_type} "
                            f"p.{citation.page_reference}",
                source_url=document.url,
                data_status="MANUAL",
                observed_at=datetime.now(tz=timezone.utc),
            ))
            return "ipo_risk_factors"

        if citation.citation_type != "FIGURE" or not citation.period_label:
            return None

        if document.ipo_id:
            return self._promote_ipo_financial(db, document, citation)
        if document.symbol:
            return self._promote_statement(db, document, citation)
        return None

    @staticmethod
    def _promote_ipo_financial(db: Session, document: ResearchDocument,
                               citation: ResearchCitation) -> Optional[str]:
        row = db.execute(
            select(IpoFinancials)
            .where(IpoFinancials.ipo_id == document.ipo_id)
            .where(IpoFinancials.period_label == citation.period_label)
        ).scalars().first()
        if row is None:
            row = IpoFinancials(
                ipo_id=document.ipo_id, period_label=citation.period_label,
                provider="pipeline",
                source_name=f"{document.title or document.doc_type}",
                source_url=document.url, data_status="MANUAL",
                observed_at=datetime.now(tz=timezone.utc),
            )
            db.add(row)

        if citation.metric_key and hasattr(row, citation.metric_key):
            setattr(row, citation.metric_key, citation.metric_value)
            row.citation_id = citation.id
            _restamp_provenance(row, document)
            return f"ipo_financials.{citation.metric_key}"
        return None

    @staticmethod
    def _promote_statement(db: Session, document: ResearchDocument,
                           citation: ResearchCitation) -> Optional[str]:
        from app.models.fundamental import FinancialStatement

        period_end = _period_end(citation.period_label)
        if period_end is None:
            return None

        row = db.execute(
            select(FinancialStatement)
            .where(FinancialStatement.symbol == document.symbol)
            .where(FinancialStatement.period_label == citation.period_label)
            .where(FinancialStatement.statement_type == "PNL")
        ).scalars().first()
        if row is None:
            row = FinancialStatement(
                symbol=document.symbol,
                period_type="ANNUAL" if citation.period_label.startswith("FY")
                else "QUARTER",
                period_end=period_end,
                period_label=citation.period_label,
                statement_type="PNL",
                provider="pipeline",
                source_name=document.title or document.doc_type,
                source_url=document.url,
                data_status="MANUAL",
                observed_at=datetime.now(tz=timezone.utc),
                published_at=document.document_date,
            )
            db.add(row)

        if citation.metric_key and hasattr(row, citation.metric_key):
            setattr(row, citation.metric_key, citation.metric_value)
            _restamp_provenance(row, document)
            return f"financial_statements.{citation.metric_key}"
        return None


def _restamp_provenance(row: Any, document: ResearchDocument) -> None:
    """Re-badge a row that has just received an operator-approved figure.

    Without this, approving a cited figure into a seeded row would leave it
    badged DEMO - the UI would show a human-verified number from a real filing
    as demonstration data, which is exactly the kind of quiet mislabelling this
    platform exists to prevent.
    """
    row.provider = "pipeline"
    row.source_name = (
        f"{document.title or document.doc_type} (operator-approved extraction)"
    )
    row.source_url = document.url
    row.data_status = "MANUAL"
    row.observed_at = datetime.now(tz=timezone.utc)
    if hasattr(row, "is_demo"):
        row.is_demo = False


def _period_end(label: Optional[str]) -> Optional[date]:
    """FY25 -> 31 March 2025. Q2FY25 -> the quarter end."""
    if not label:
        return None
    import re

    quarter = re.fullmatch(r"Q([1-4])FY(\d{2})", label, re.IGNORECASE)
    if quarter:
        year = 2000 + int(quarter.group(2))
        month_day = {1: (6, 30), 2: (9, 30), 3: (12, 31), 4: (3, 31)}[
            int(quarter.group(1))
        ]
        # Q4 falls in the same calendar year as the fiscal year label.
        calendar_year = year if int(quarter.group(1)) == 4 else year - 1
        return date(calendar_year, *month_day)

    annual = re.fullmatch(r"FY(\d{2})", label, re.IGNORECASE)
    if annual:
        return date(2000 + int(annual.group(1)), 3, 31)
    return None


document_pipeline = DocumentPipeline()
