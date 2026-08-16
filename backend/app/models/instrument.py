"""Instrument master, exchanges, indices and the market calendar.

The instrument universe is *imported*, never hard-coded. `Instrument` is the
single row type for equities, indices, futures and options - `segment`
discriminates. Option/future specific columns are nullable.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import Boolean, Date, Float, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Provenance, Timestamped, UTCDateTime, UUIDPrimaryKey


class Exchange(Base, UUIDPrimaryKey, Timestamped):
    __tablename__ = "exchanges"

    code: Mapped[str] = mapped_column(String(10), unique=True, nullable=False)  # NSE/BSE
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    country: Mapped[str] = mapped_column(String(4), default="IN")
    timezone: Mapped[str] = mapped_column(String(40), default="Asia/Kolkata")
    yahoo_suffix: Mapped[str] = mapped_column(String(8), default="")  # .NS / .BO
    website: Mapped[Optional[str]] = mapped_column(String(300))


class Instrument(Base, UUIDPrimaryKey, Timestamped, Provenance):
    __tablename__ = "instruments"
    __table_args__ = (
        UniqueConstraint("exchange_code", "symbol", "segment", "expiry", "strike",
                         "option_type", name="uq_instrument_contract"),
        Index("ix_instrument_symbol_segment", "symbol", "segment"),
        Index("ix_instrument_isin", "isin"),
        Index("ix_instrument_underlying", "underlying_symbol"),
    )

    symbol: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(250), default="")
    exchange_code: Mapped[str] = mapped_column(String(10), default="NSE", nullable=False)
    segment: Mapped[str] = mapped_column(String(12), default="EQUITY", nullable=False)
    # EQUITY | INDEX | FUTURE | OPTION

    isin: Mapped[Optional[str]] = mapped_column(String(20))
    nse_code: Mapped[Optional[str]] = mapped_column(String(60))
    bse_code: Mapped[Optional[str]] = mapped_column(String(20))
    series: Mapped[Optional[str]] = mapped_column(String(10))  # EQ, BE, ...
    industry: Mapped[Optional[str]] = mapped_column(String(160))
    sector: Mapped[Optional[str]] = mapped_column(String(160))
    market_cap_band: Mapped[Optional[str]] = mapped_column(String(20))  # LARGE/MID/SMALL

    # Derivatives
    underlying_symbol: Mapped[Optional[str]] = mapped_column(String(60))
    expiry: Mapped[Optional[date]] = mapped_column(Date)
    strike: Mapped[Optional[float]] = mapped_column(Float)
    option_type: Mapped[Optional[str]] = mapped_column(String(2))  # CE | PE
    lot_size: Mapped[Optional[int]] = mapped_column(Integer)
    tick_size: Mapped[Optional[float]] = mapped_column(Float)

    is_fno_eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    listed_on: Mapped[Optional[date]] = mapped_column(Date)

    @property
    def display_name(self) -> str:
        if self.segment == "OPTION" and self.expiry and self.strike:
            return (f"{self.underlying_symbol} {self.expiry:%d %b %y} "
                    f"{self.strike:g} {self.option_type}")
        if self.segment == "FUTURE" and self.expiry:
            return f"{self.underlying_symbol} FUT {self.expiry:%d %b %y}"
        return self.symbol

    @property
    def yahoo_ticker(self) -> str:
        suffix = ".BO" if self.exchange_code == "BSE" else ".NS"
        return f"{self.symbol}{suffix}"


class IndexConstituent(Base, UUIDPrimaryKey, Timestamped, Provenance):
    """Index membership with weights - drives sector contribution analysis."""

    __tablename__ = "index_constituents"
    __table_args__ = (
        UniqueConstraint("index_symbol", "constituent_symbol", "as_of",
                         name="uq_index_member"),
    )

    index_symbol: Mapped[str] = mapped_column(String(60), index=True, nullable=False)
    constituent_symbol: Mapped[str] = mapped_column(String(60), nullable=False)
    weight_pct: Mapped[Optional[float]] = mapped_column(Float)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)


class MarketHoliday(Base, UUIDPrimaryKey, Timestamped, Provenance):
    """Holidays are data. Never hard-code them into the calendar module."""

    __tablename__ = "market_holidays"
    __table_args__ = (
        UniqueConstraint("exchange_code", "holiday_date", "segment",
                         name="uq_holiday"),
    )

    exchange_code: Mapped[str] = mapped_column(String(10), default="NSE")
    segment: Mapped[str] = mapped_column(String(12), default="EQUITY")
    holiday_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    description: Mapped[str] = mapped_column(String(200), default="")


class InstrumentSyncRun(Base, UUIDPrimaryKey, Timestamped):
    """Audit trail for instrument-master imports."""

    __tablename__ = "instrument_sync_runs"

    provider: Mapped[str] = mapped_column(String(60), nullable=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    finished_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    records_received: Mapped[int] = mapped_column(Integer, default=0)
    records_saved: Mapped[int] = mapped_column(Integer, default=0)
    records_rejected: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="RUNNING")
    error: Mapped[Optional[str]] = mapped_column(String(2000))
