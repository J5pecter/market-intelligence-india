"""Fundamentals, financial statements, shareholding and corporate actions."""

from __future__ import annotations

from datetime import date
from typing import Optional

from sqlalchemy import Date, Float, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Provenance, Timestamped, UUIDPrimaryKey


class CompanyProfile(Base, UUIDPrimaryKey, Timestamped, Provenance):
    __tablename__ = "company_profiles"

    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("instruments.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(60), index=True, nullable=False)

    description: Mapped[Optional[str]] = mapped_column(Text)
    industry: Mapped[Optional[str]] = mapped_column(String(160))
    sector: Mapped[Optional[str]] = mapped_column(String(160))
    website: Mapped[Optional[str]] = mapped_column(String(300))
    employees: Mapped[Optional[int]] = mapped_column()
    incorporated_year: Mapped[Optional[int]] = mapped_column()
    products: Mapped[Optional[str]] = mapped_column(Text)          # JSON list
    business_segments: Mapped[Optional[str]] = mapped_column(Text)  # JSON list
    geographies: Mapped[Optional[str]] = mapped_column(Text)        # JSON list
    competitive_position: Mapped[Optional[str]] = mapped_column(Text)
    peers: Mapped[Optional[str]] = mapped_column(Text)              # JSON list of symbols


class Fundamental(Base, UUIDPrimaryKey, Timestamped, Provenance):
    """Latest ratio snapshot. Statement-level detail lives in
    `financial_statements`; this table is what the header strip reads."""

    __tablename__ = "fundamentals"

    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("instruments.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(60), index=True, nullable=False)
    as_of: Mapped[Optional[date]] = mapped_column(Date)

    market_cap: Mapped[Optional[float]] = mapped_column(Float)
    enterprise_value: Mapped[Optional[float]] = mapped_column(Float)
    pe: Mapped[Optional[float]] = mapped_column(Float)
    forward_pe: Mapped[Optional[float]] = mapped_column(Float)
    pb: Mapped[Optional[float]] = mapped_column(Float)
    ev_ebitda: Mapped[Optional[float]] = mapped_column(Float)
    ev_sales: Mapped[Optional[float]] = mapped_column(Float)
    peg: Mapped[Optional[float]] = mapped_column(Float)
    eps_ttm: Mapped[Optional[float]] = mapped_column(Float)
    book_value: Mapped[Optional[float]] = mapped_column(Float)
    dividend_yield: Mapped[Optional[float]] = mapped_column(Float)
    roe: Mapped[Optional[float]] = mapped_column(Float)
    roce: Mapped[Optional[float]] = mapped_column(Float)
    roa: Mapped[Optional[float]] = mapped_column(Float)
    debt_to_equity: Mapped[Optional[float]] = mapped_column(Float)
    interest_coverage: Mapped[Optional[float]] = mapped_column(Float)
    current_ratio: Mapped[Optional[float]] = mapped_column(Float)
    ebitda_margin: Mapped[Optional[float]] = mapped_column(Float)
    net_margin: Mapped[Optional[float]] = mapped_column(Float)
    revenue_cagr_3y: Mapped[Optional[float]] = mapped_column(Float)
    pat_cagr_3y: Mapped[Optional[float]] = mapped_column(Float)
    beta: Mapped[Optional[float]] = mapped_column(Float)

    promoter_holding: Mapped[Optional[float]] = mapped_column(Float)
    fii_holding: Mapped[Optional[float]] = mapped_column(Float)
    dii_holding: Mapped[Optional[float]] = mapped_column(Float)
    public_holding: Mapped[Optional[float]] = mapped_column(Float)
    promoter_pledge: Mapped[Optional[float]] = mapped_column(Float)


class FinancialStatement(Base, UUIDPrimaryKey, Timestamped, Provenance):
    """One row per company per period per statement type."""

    __tablename__ = "financial_statements"
    __table_args__ = (
        UniqueConstraint("symbol", "period_type", "period_end", "statement_type",
                         name="uq_statement"),
        Index("ix_statement_symbol_period", "symbol", "period_end"),
    )

    instrument_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("instruments.id", ondelete="CASCADE")
    )
    symbol: Mapped[str] = mapped_column(String(60), index=True, nullable=False)
    period_type: Mapped[str] = mapped_column(String(10), default="ANNUAL")  # ANNUAL|QUARTER|TTM
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    period_label: Mapped[str] = mapped_column(String(20), default="")       # FY24, Q2FY25
    statement_type: Mapped[str] = mapped_column(String(20), default="PNL")  # PNL|BS|CF
    currency: Mapped[str] = mapped_column(String(4), default="INR")
    unit_multiplier: Mapped[float] = mapped_column(Float, default=1.0)      # to absolute INR

    revenue: Mapped[Optional[float]] = mapped_column(Float)
    other_income: Mapped[Optional[float]] = mapped_column(Float)
    ebitda: Mapped[Optional[float]] = mapped_column(Float)
    ebitda_margin: Mapped[Optional[float]] = mapped_column(Float)
    ebit: Mapped[Optional[float]] = mapped_column(Float)
    interest: Mapped[Optional[float]] = mapped_column(Float)
    depreciation: Mapped[Optional[float]] = mapped_column(Float)
    pbt: Mapped[Optional[float]] = mapped_column(Float)
    tax: Mapped[Optional[float]] = mapped_column(Float)
    pat: Mapped[Optional[float]] = mapped_column(Float)
    eps: Mapped[Optional[float]] = mapped_column(Float)

    total_assets: Mapped[Optional[float]] = mapped_column(Float)
    total_debt: Mapped[Optional[float]] = mapped_column(Float)
    cash_and_equivalents: Mapped[Optional[float]] = mapped_column(Float)
    net_worth: Mapped[Optional[float]] = mapped_column(Float)
    working_capital: Mapped[Optional[float]] = mapped_column(Float)

    operating_cash_flow: Mapped[Optional[float]] = mapped_column(Float)
    investing_cash_flow: Mapped[Optional[float]] = mapped_column(Float)
    financing_cash_flow: Mapped[Optional[float]] = mapped_column(Float)
    capex: Mapped[Optional[float]] = mapped_column(Float)
    free_cash_flow: Mapped[Optional[float]] = mapped_column(Float)

    is_restated: Mapped[bool] = mapped_column(default=False)
    # Timestamp the market could first have seen this - backtests filter on it
    # so a FY result published in May is never visible in April.
    published_at: Mapped[Optional[date]] = mapped_column(Date)


class Shareholding(Base, UUIDPrimaryKey, Timestamped, Provenance):
    __tablename__ = "shareholding"
    __table_args__ = (
        UniqueConstraint("symbol", "as_of", name="uq_shareholding"),
    )

    symbol: Mapped[str] = mapped_column(String(60), index=True, nullable=False)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)
    promoter: Mapped[Optional[float]] = mapped_column(Float)
    promoter_pledged: Mapped[Optional[float]] = mapped_column(Float)
    fii: Mapped[Optional[float]] = mapped_column(Float)
    dii: Mapped[Optional[float]] = mapped_column(Float)
    mutual_funds: Mapped[Optional[float]] = mapped_column(Float)
    insurance: Mapped[Optional[float]] = mapped_column(Float)
    public: Mapped[Optional[float]] = mapped_column(Float)
    others: Mapped[Optional[float]] = mapped_column(Float)


class CorporateAction(Base, UUIDPrimaryKey, Timestamped, Provenance):
    __tablename__ = "corporate_actions"
    __table_args__ = (
        Index("ix_ca_symbol_exdate", "symbol", "ex_date"),
    )

    symbol: Mapped[str] = mapped_column(String(60), index=True, nullable=False)
    action_type: Mapped[str] = mapped_column(String(30), nullable=False)
    # DIVIDEND|BONUS|SPLIT|RIGHTS|BUYBACK|MERGER|DEMERGER|DELISTING|OPEN_OFFER
    description: Mapped[str] = mapped_column(String(500), default="")
    announcement_date: Mapped[Optional[date]] = mapped_column(Date)
    ex_date: Mapped[Optional[date]] = mapped_column(Date, index=True)
    record_date: Mapped[Optional[date]] = mapped_column(Date)
    payment_date: Mapped[Optional[date]] = mapped_column(Date)
    value: Mapped[Optional[float]] = mapped_column(Float)     # dividend per share
    ratio_from: Mapped[Optional[float]] = mapped_column(Float)
    ratio_to: Mapped[Optional[float]] = mapped_column(Float)
    # Price adjustment factor applied to historical bars before this ex-date.
    price_adjustment_factor: Mapped[Optional[float]] = mapped_column(Float)


class EarningsEvent(Base, UUIDPrimaryKey, Timestamped, Provenance):
    """Results calendar row - expected date, then actuals once reported."""

    __tablename__ = "earnings_events"
    __table_args__ = (
        UniqueConstraint("symbol", "quarter_label", name="uq_earnings_quarter"),
        Index("ix_earnings_expected", "expected_date"),
    )

    symbol: Mapped[str] = mapped_column(String(60), index=True, nullable=False)
    quarter_label: Mapped[str] = mapped_column(String(20), nullable=False)  # Q2FY26
    expected_date: Mapped[Optional[date]] = mapped_column(Date)
    reported_date: Mapped[Optional[date]] = mapped_column(Date)
    board_meeting_date: Mapped[Optional[date]] = mapped_column(Date)

    revenue: Mapped[Optional[float]] = mapped_column(Float)
    pat: Mapped[Optional[float]] = mapped_column(Float)
    eps: Mapped[Optional[float]] = mapped_column(Float)
    revenue_yoy_pct: Mapped[Optional[float]] = mapped_column(Float)
    revenue_qoq_pct: Mapped[Optional[float]] = mapped_column(Float)
    pat_yoy_pct: Mapped[Optional[float]] = mapped_column(Float)
    pat_qoq_pct: Mapped[Optional[float]] = mapped_column(Float)

    consensus_revenue: Mapped[Optional[float]] = mapped_column(Float)
    consensus_pat: Mapped[Optional[float]] = mapped_column(Float)
    consensus_source: Mapped[Optional[str]] = mapped_column(String(200))

    price_reaction_1d_pct: Mapped[Optional[float]] = mapped_column(Float)
    price_reaction_5d_pct: Mapped[Optional[float]] = mapped_column(Float)
    management_commentary: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="SCHEDULED")
