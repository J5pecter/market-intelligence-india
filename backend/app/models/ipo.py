"""IPO master, GMP history, subscription, extracted fundamentals and scoring."""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import (Boolean, Date, Float, ForeignKey, Index, Integer,
                        String, Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Provenance, Timestamped, UTCDateTime, UUIDPrimaryKey


class Ipo(Base, UUIDPrimaryKey, Timestamped, Provenance):
    __tablename__ = "ipos"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_ipo_slug"),
        Index("ix_ipo_open_date", "open_date"),
    )

    slug: Mapped[str] = mapped_column(String(160), nullable=False)
    company_name: Mapped[str] = mapped_column(String(300), nullable=False)
    symbol: Mapped[Optional[str]] = mapped_column(String(60))

    status: Mapped[str] = mapped_column(String(20), default="UPCOMING")
    # UPCOMING|OPEN|CLOSED|ALLOTMENT|LISTED|WITHDRAWN
    ipo_type: Mapped[str] = mapped_column(String(20), default="MAINBOARD")  # MAINBOARD|SME

    open_date: Mapped[Optional[date]] = mapped_column(Date)
    close_date: Mapped[Optional[date]] = mapped_column(Date)
    allotment_date: Mapped[Optional[date]] = mapped_column(Date)
    refund_date: Mapped[Optional[date]] = mapped_column(Date)
    demat_date: Mapped[Optional[date]] = mapped_column(Date)
    listing_date: Mapped[Optional[date]] = mapped_column(Date)

    price_band_low: Mapped[Optional[float]] = mapped_column(Float)
    price_band_high: Mapped[Optional[float]] = mapped_column(Float)
    face_value: Mapped[Optional[float]] = mapped_column(Float)
    lot_size: Mapped[Optional[int]] = mapped_column(Integer)
    retail_min_investment: Mapped[Optional[float]] = mapped_column(Float)

    issue_size_cr: Mapped[Optional[float]] = mapped_column(Float)
    fresh_issue_cr: Mapped[Optional[float]] = mapped_column(Float)
    ofs_cr: Mapped[Optional[float]] = mapped_column(Float)
    promoter_selling_note: Mapped[Optional[str]] = mapped_column(Text)
    use_of_proceeds: Mapped[Optional[str]] = mapped_column(Text)   # JSON list

    lead_managers: Mapped[Optional[str]] = mapped_column(Text)     # JSON list
    registrar: Mapped[Optional[str]] = mapped_column(String(300))
    listing_exchanges: Mapped[Optional[str]] = mapped_column(String(60))
    industry: Mapped[Optional[str]] = mapped_column(String(200))

    listing_price: Mapped[Optional[float]] = mapped_column(Float)
    listing_gain_pct: Mapped[Optional[float]] = mapped_column(Float)

    anchor_investment_cr: Mapped[Optional[float]] = mapped_column(Float)
    anchor_investors: Mapped[Optional[str]] = mapped_column(Text)  # JSON list


class IpoGmpHistory(Base, UUIDPrimaryKey, Timestamped, Provenance):
    """Grey-market premium is an unofficial indicator. Every row carries its
    own source and timestamp; the UI never shows the number bare."""

    __tablename__ = "ipo_gmp_history"
    __table_args__ = (
        UniqueConstraint("ipo_id", "observed_on", "provider", name="uq_gmp_point"),
        Index("ix_gmp_ipo_time", "ipo_id", "observed_on"),
    )

    ipo_id: Mapped[str] = mapped_column(
        ForeignKey("ipos.id", ondelete="CASCADE"), index=True, nullable=False
    )
    observed_on: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    gmp: Mapped[Optional[float]] = mapped_column(Float)
    gmp_pct: Mapped[Optional[float]] = mapped_column(Float)
    estimated_listing_price: Mapped[Optional[float]] = mapped_column(Float)
    reference_price: Mapped[Optional[float]] = mapped_column(Float)
    kostak: Mapped[Optional[float]] = mapped_column(Float)
    subject_to_sauda: Mapped[Optional[float]] = mapped_column(Float)
    confidence_note: Mapped[str] = mapped_column(
        String(400),
        default="Unofficial grey-market indicator. Not an exchange price.",
    )


class IpoSubscription(Base, UUIDPrimaryKey, Timestamped, Provenance):
    __tablename__ = "ipo_subscription"
    __table_args__ = (
        UniqueConstraint("ipo_id", "observed_at", name="uq_ipo_sub_point"),
    )

    ipo_id: Mapped[str] = mapped_column(
        ForeignKey("ipos.id", ondelete="CASCADE"), index=True, nullable=False
    )
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    day_number: Mapped[Optional[int]] = mapped_column(Integer)
    qib_times: Mapped[Optional[float]] = mapped_column(Float)
    nii_times: Mapped[Optional[float]] = mapped_column(Float)
    retail_times: Mapped[Optional[float]] = mapped_column(Float)
    employee_times: Mapped[Optional[float]] = mapped_column(Float)
    shareholder_times: Mapped[Optional[float]] = mapped_column(Float)
    total_times: Mapped[Optional[float]] = mapped_column(Float)


class IpoFinancials(Base, UUIDPrimaryKey, Timestamped, Provenance):
    """Extracted from the offer document. Every field should have a citation."""

    __tablename__ = "ipo_financials"
    __table_args__ = (
        UniqueConstraint("ipo_id", "period_label", name="uq_ipo_fin_period"),
    )

    ipo_id: Mapped[str] = mapped_column(
        ForeignKey("ipos.id", ondelete="CASCADE"), index=True, nullable=False
    )
    period_label: Mapped[str] = mapped_column(String(20), nullable=False)
    period_end: Mapped[Optional[date]] = mapped_column(Date)

    revenue: Mapped[Optional[float]] = mapped_column(Float)
    ebitda: Mapped[Optional[float]] = mapped_column(Float)
    ebitda_margin: Mapped[Optional[float]] = mapped_column(Float)
    pat: Mapped[Optional[float]] = mapped_column(Float)
    net_margin: Mapped[Optional[float]] = mapped_column(Float)
    eps: Mapped[Optional[float]] = mapped_column(Float)
    net_worth: Mapped[Optional[float]] = mapped_column(Float)
    total_debt: Mapped[Optional[float]] = mapped_column(Float)
    cash: Mapped[Optional[float]] = mapped_column(Float)
    working_capital: Mapped[Optional[float]] = mapped_column(Float)
    roe: Mapped[Optional[float]] = mapped_column(Float)
    roce: Mapped[Optional[float]] = mapped_column(Float)
    citation_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("research_citations.id", ondelete="SET NULL")
    )


class IpoRiskFactor(Base, UUIDPrimaryKey, Timestamped, Provenance):
    """Concentration, litigation, related-party, contingent liabilities."""

    __tablename__ = "ipo_risk_factors"

    ipo_id: Mapped[str] = mapped_column(
        ForeignKey("ipos.id", ondelete="CASCADE"), index=True, nullable=False
    )
    category: Mapped[str] = mapped_column(String(60), nullable=False)
    # CUSTOMER_CONCENTRATION|SUPPLIER_CONCENTRATION|GEOGRAPHIC|LITIGATION|
    # CONTINGENT_LIABILITY|RELATED_PARTY|REGULATORY|PROMOTER|LEVERAGE|OTHER
    description: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(10), default="MEDIUM")
    quantum: Mapped[Optional[float]] = mapped_column(Float)
    quantum_unit: Mapped[Optional[str]] = mapped_column(String(30))
    citation_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("research_citations.id", ondelete="SET NULL")
    )


class IpoAnalysis(Base, UUIDPrimaryKey, Timestamped):
    """Component scores + label. Never a bare SUBSCRIBE/AVOID."""

    __tablename__ = "ipo_analysis"
    __table_args__ = (
        UniqueConstraint("ipo_id", "version", name="uq_ipo_analysis_version"),
    )

    ipo_id: Mapped[str] = mapped_column(
        ForeignKey("ipos.id", ondelete="CASCADE"), index=True, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, default=1)

    business_quality: Mapped[Optional[float]] = mapped_column(Float)
    financial_quality: Mapped[Optional[float]] = mapped_column(Float)
    valuation_attractiveness: Mapped[Optional[float]] = mapped_column(Float)
    gmp_signal: Mapped[Optional[float]] = mapped_column(Float)
    subscription_strength: Mapped[Optional[float]] = mapped_column(Float)
    risk_score: Mapped[Optional[float]] = mapped_column(Float)
    overall_research_score: Mapped[Optional[float]] = mapped_column(Float)

    label: Mapped[str] = mapped_column(String(60), default="Insufficient data")
    data_completeness_pct: Mapped[Optional[float]] = mapped_column(Float)

    swot_json: Mapped[Optional[str]] = mapped_column(Text)
    valuation_json: Mapped[Optional[str]] = mapped_column(Text)
    peer_comparison_json: Mapped[Optional[str]] = mapped_column(Text)
    evidence_json: Mapped[Optional[str]] = mapped_column(Text)
    generated_by: Mapped[str] = mapped_column(String(40), default="PLATFORM")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
