"""Research calls, sources, documents, citations, signals and performance.

The distinction the whole product hangs on:

* `source_type = EXTERNAL_RESEARCH` - somebody else published this. We reproduce
  it with attribution, the original levels, and whatever transformation we
  applied stated explicitly.
* `source_type = PLATFORM_GENERATED` - our engines produced it from the evidence
  in `evidence_json`. Never presented as advice.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import (Boolean, Date, Float, ForeignKey, Index, Integer,
                        String, Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Provenance, Timestamped, UTCDateTime, UUIDPrimaryKey


class ResearchSource(Base, UUIDPrimaryKey, Timestamped):
    """A publisher of external research, or the platform itself."""

    __tablename__ = "research_sources"

    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    source_type: Mapped[str] = mapped_column(String(30), default="EXTERNAL_RESEARCH")
    organisation: Mapped[Optional[str]] = mapped_column(String(200))
    website: Mapped[Optional[str]] = mapped_column(String(400))
    registration_note: Mapped[Optional[str]] = mapped_column(String(400))
    reliability: Mapped[str] = mapped_column(String(10), default="UNKNOWN")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    licence_note: Mapped[Optional[str]] = mapped_column(Text)


class ResearchCall(Base, UUIDPrimaryKey, Timestamped, Provenance):
    __tablename__ = "research_calls"
    __table_args__ = (
        Index("ix_call_symbol_status", "symbol", "status"),
        Index("ix_call_published", "published_at"),
    )

    # --- identity ---------------------------------------------------------
    instrument_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("instruments.id", ondelete="SET NULL")
    )
    symbol: Mapped[str] = mapped_column(String(60), index=True, nullable=False)
    company_name: Mapped[str] = mapped_column(String(250), default="")
    segment: Mapped[str] = mapped_column(String(12), default="EQUITY")
    expiry: Mapped[Optional[date]] = mapped_column(Date)
    strike: Mapped[Optional[float]] = mapped_column(Float)
    option_type: Mapped[Optional[str]] = mapped_column(String(2))
    lot_size: Mapped[Optional[int]] = mapped_column(Integer)

    # --- provenance -------------------------------------------------------
    source_type: Mapped[str] = mapped_column(String(30), default="PLATFORM_GENERATED")
    # EXTERNAL_RESEARCH | PLATFORM_GENERATED
    source_id: Mapped[Optional[str]] = mapped_column(ForeignKey("research_sources.id"))
    source_name: Mapped[str] = mapped_column(String(200), default="")
    analyst_name: Mapped[Optional[str]] = mapped_column(String(200))
    original_url: Mapped[Optional[str]] = mapped_column(String(1200))
    published_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    valid_until: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)

    # If we changed anything from the original publication, say what and why.
    was_transformed: Mapped[bool] = mapped_column(Boolean, default=False)
    transformation_note: Mapped[Optional[str]] = mapped_column(Text)
    original_recommendation: Mapped[Optional[str]] = mapped_column(String(2000))

    # --- the setup --------------------------------------------------------
    side: Mapped[str] = mapped_column(String(10), default="WATCH")  # BUY|SELL|WATCH|MIXED
    entry_min: Mapped[Optional[float]] = mapped_column(Float)
    entry_max: Mapped[Optional[float]] = mapped_column(Float)
    stop_loss: Mapped[Optional[float]] = mapped_column(Float)
    target_1: Mapped[Optional[float]] = mapped_column(Float)
    target_2: Mapped[Optional[float]] = mapped_column(Float)
    target_3: Mapped[Optional[float]] = mapped_column(Float)
    horizon: Mapped[Optional[str]] = mapped_column(String(30))   # INTRADAY|SWING|POSITIONAL|LONG_TERM
    timeframe: Mapped[Optional[str]] = mapped_column(String(10))

    # --- lifecycle --------------------------------------------------------
    lifecycle_state: Mapped[str] = mapped_column(String(20), default="CREATED")
    # CREATED|PUBLISHED|ACTIVE|MODIFIED|TARGET_REACHED|STOP_LOSS|EXPIRED|CLOSED|ARCHIVED
    status: Mapped[str] = mapped_column(String(30), default="NOT_ACTIVATED")
    # NOT_ACTIVATED|WITHIN_ENTRY|ABOVE_ENTRY|TARGET_IN_PROGRESS|TARGET_ACHIEVED|
    # STOP_LOSS_TRIGGERED|EXPIRED|INVALIDATED
    status_reason: Mapped[Optional[str]] = mapped_column(String(600))
    version: Mapped[int] = mapped_column(Integer, default=1)

    # --- derived (recomputed by the status engine, never hand-edited) -----
    reference_price: Mapped[Optional[float]] = mapped_column(Float)  # LTP at last eval
    achieved_pct: Mapped[Optional[float]] = mapped_column(Float)
    potential_pct: Mapped[Optional[float]] = mapped_column(Float)
    risk_reward: Mapped[Optional[float]] = mapped_column(Float)
    risk_rating: Mapped[Optional[str]] = mapped_column(String(20))
    confidence: Mapped[Optional[float]] = mapped_column(Float)

    # --- reasoning --------------------------------------------------------
    rationale: Mapped[Optional[str]] = mapped_column(Text)
    invalidation: Mapped[Optional[str]] = mapped_column(Text)
    why_now: Mapped[Optional[str]] = mapped_column(Text)      # JSON list of evidence
    why_not: Mapped[Optional[str]] = mapped_column(Text)      # JSON list of counter-evidence
    evidence_json: Mapped[Optional[str]] = mapped_column(Text)  # full evidence chain
    catalysts_json: Mapped[Optional[str]] = mapped_column(Text)

    is_published: Mapped[bool] = mapped_column(Boolean, default=False)
    approved_by: Mapped[Optional[str]] = mapped_column(String(120))
    approved_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)


class ResearchCallVersion(Base, UUIDPrimaryKey, Timestamped):
    """Immutable history. Published records are never overwritten silently."""

    __tablename__ = "research_call_versions"
    __table_args__ = (
        UniqueConstraint("call_id", "version", name="uq_call_version"),
    )

    call_id: Mapped[str] = mapped_column(
        ForeignKey("research_calls.id", ondelete="CASCADE"), index=True, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    changed_fields: Mapped[Optional[str]] = mapped_column(Text)  # JSON diff
    changed_by: Mapped[Optional[str]] = mapped_column(String(120))
    change_reason: Mapped[Optional[str]] = mapped_column(String(1000))


class ResearchCallPerformance(Base, UUIDPrimaryKey, Timestamped):
    """Tracked forward from publication. Written by a job, not by hand."""

    __tablename__ = "signal_performance"
    __table_args__ = (UniqueConstraint("call_id", name="uq_call_perf"),)

    call_id: Mapped[str] = mapped_column(
        ForeignKey("research_calls.id", ondelete="CASCADE"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(60), index=True, nullable=False)
    source_name: Mapped[str] = mapped_column(String(200), default="")

    price_at_publication: Mapped[Optional[float]] = mapped_column(Float)
    published_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)

    max_favourable_excursion_pct: Mapped[Optional[float]] = mapped_column(Float)
    max_adverse_excursion_pct: Mapped[Optional[float]] = mapped_column(Float)
    target_hit: Mapped[Optional[bool]] = mapped_column(Boolean)
    stop_hit: Mapped[Optional[bool]] = mapped_column(Boolean)
    time_to_target_days: Mapped[Optional[float]] = mapped_column(Float)
    time_to_stop_days: Mapped[Optional[float]] = mapped_column(Float)

    return_1d_pct: Mapped[Optional[float]] = mapped_column(Float)
    return_3d_pct: Mapped[Optional[float]] = mapped_column(Float)
    return_7d_pct: Mapped[Optional[float]] = mapped_column(Float)
    return_30d_pct: Mapped[Optional[float]] = mapped_column(Float)
    current_return_pct: Mapped[Optional[float]] = mapped_column(Float)
    last_evaluated_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)


class ResearchDocument(Base, UUIDPrimaryKey, Timestamped, Provenance):
    """Annual reports, presentations, filings, DRHP/RHP."""

    __tablename__ = "research_documents"
    __table_args__ = (Index("ix_doc_symbol_type", "symbol", "doc_type"),)

    symbol: Mapped[Optional[str]] = mapped_column(String(60), index=True)
    ipo_id: Mapped[Optional[str]] = mapped_column(ForeignKey("ipos.id",
                                                             ondelete="CASCADE"))
    doc_type: Mapped[str] = mapped_column(String(40), nullable=False)
    # ANNUAL_REPORT|QUARTERLY_RESULT|INVESTOR_PRESENTATION|EARNINGS_RELEASE|
    # TRANSCRIPT|EXCHANGE_FILING|ANNOUNCEMENT|DRHP|RHP|OFFER_DOCUMENT|
    # CREDIT_RATING|SHAREHOLDING
    title: Mapped[str] = mapped_column(String(500), default="")
    document_date: Mapped[Optional[date]] = mapped_column(Date)
    url: Mapped[Optional[str]] = mapped_column(String(1500))
    local_path: Mapped[Optional[str]] = mapped_column(String(1000))
    page_count: Mapped[Optional[int]] = mapped_column(Integer)
    extraction_status: Mapped[str] = mapped_column(String(20), default="NOT_STARTED")
    extraction_note: Mapped[Optional[str]] = mapped_column(Text)


class ResearchCitation(Base, UUIDPrimaryKey, Timestamped):
    """Every extracted claim points back at a document, page and date.

    Machine extractions land here with `review_status='PENDING'` and go no
    further until a human approves them. Nothing written by the pipeline
    reaches the fundamentals tables on its own.
    """

    __tablename__ = "research_citations"
    __table_args__ = (
        Index("ix_citation_review", "review_status", "extracted_by"),
    )

    document_id: Mapped[str] = mapped_column(
        ForeignKey("research_documents.id", ondelete="CASCADE"), index=True,
        nullable=False,
    )
    call_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("research_calls.id", ondelete="SET NULL")
    )
    claim: Mapped[str] = mapped_column(Text, nullable=False)
    citation_type: Mapped[str] = mapped_column(String(24), default="FIGURE")
    # FIGURE | COMMENTARY | RISK_FACTOR | USE_OF_PROCEEDS

    metric_key: Mapped[Optional[str]] = mapped_column(String(80))
    metric_value: Mapped[Optional[float]] = mapped_column(Float)
    # Both sides of the unit conversion are kept so a reviewer can see exactly
    # what was printed and what the multiplier turned it into.
    raw_value: Mapped[Optional[float]] = mapped_column(Float)
    normalised_value: Mapped[Optional[float]] = mapped_column(Float)
    unit: Mapped[Optional[str]] = mapped_column(String(20))
    unit_multiplier: Mapped[Optional[float]] = mapped_column(Float)
    period_label: Mapped[Optional[str]] = mapped_column(String(20))

    page_reference: Mapped[Optional[str]] = mapped_column(String(80))
    section: Mapped[Optional[str]] = mapped_column(String(300))
    quote: Mapped[Optional[str]] = mapped_column(Text)
    extracted_by: Mapped[str] = mapped_column(String(40), default="MANUAL")
    confidence: Mapped[Optional[float]] = mapped_column(Float)
    confidence_reasons: Mapped[Optional[str]] = mapped_column(Text)  # JSON list

    review_status: Mapped[str] = mapped_column(String(16), default="PENDING")
    # PENDING | APPROVED | REJECTED
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(120))
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)


class Signal(Base, UUIDPrimaryKey, Timestamped):
    """A machine-generated observation, before it becomes a research call."""

    __tablename__ = "signals"
    __table_args__ = (Index("ix_signal_symbol_generated", "symbol", "generated_at"),)

    symbol: Mapped[str] = mapped_column(String(60), index=True, nullable=False)
    segment: Mapped[str] = mapped_column(String(12), default="EQUITY")
    signal_type: Mapped[str] = mapped_column(String(60), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), default="NEUTRAL")
    generated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    timeframe: Mapped[str] = mapped_column(String(10), default="1d")

    confidence: Mapped[Optional[float]] = mapped_column(Float)
    conflict_detected: Mapped[bool] = mapped_column(Boolean, default=False)
    rank_score: Mapped[Optional[float]] = mapped_column(Float)
    evidence_json: Mapped[Optional[str]] = mapped_column(Text)
    engine_version: Mapped[str] = mapped_column(String(20), default="1.0.0")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class ResearchReport(Base, UUIDPrimaryKey, Timestamped):
    """A rendered, versioned company report."""

    __tablename__ = "research_reports"
    __table_args__ = (
        UniqueConstraint("symbol", "version", name="uq_report_version"),
    )

    symbol: Mapped[str] = mapped_column(String(60), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(400), default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    body_json: Mapped[str] = mapped_column(Text, nullable=False)
    overall_score: Mapped[Optional[float]] = mapped_column(Float)
    generated_by: Mapped[str] = mapped_column(String(40), default="PLATFORM")
    change_summary: Mapped[Optional[str]] = mapped_column(Text)
    changed_by: Mapped[Optional[str]] = mapped_column(String(120))
    is_published: Mapped[bool] = mapped_column(Boolean, default=False)


class Catalyst(Base, UUIDPrimaryKey, Timestamped, Provenance):
    __tablename__ = "catalysts"
    __table_args__ = (Index("ix_catalyst_date", "event_date"),)

    symbol: Mapped[Optional[str]] = mapped_column(String(60), index=True)
    scope: Mapped[str] = mapped_column(String(20), default="STOCK")  # STOCK|SECTOR|MARKET
    sector: Mapped[Optional[str]] = mapped_column(String(160))
    title: Mapped[str] = mapped_column(String(400), nullable=False)
    category: Mapped[str] = mapped_column(String(40), default="OTHER")
    event_date: Mapped[Optional[date]] = mapped_column(Date)
    event_time_note: Mapped[Optional[str]] = mapped_column(String(120))
    expected_impact: Mapped[Optional[str]] = mapped_column(String(20))  # LOW|MEDIUM|HIGH
    risk_level: Mapped[Optional[str]] = mapped_column(String(20))
    historical_reaction_note: Mapped[Optional[str]] = mapped_column(Text)
    affected_instruments: Mapped[Optional[str]] = mapped_column(Text)  # JSON list
    is_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
