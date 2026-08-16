"""Official exchange end-of-day records, stored session by session.

These tables exist so the platform accumulates its own history of the
exchange's published record. That matters for one specific reason: a delivery
percentage is meaningless in isolation. 72% delivery is remarkable for an index
heavyweight and unremarkable for a utility, so judging it needs the stock's own
distribution - which only exists if somebody kept it. The exchange publishes
each day's file and then moves on; nobody backfills it for you.

Rows are keyed on (symbol, exchange, session_date) and upserted, so re-running
an ingestion for a day already stored corrects it rather than duplicating it.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from sqlalchemy import (BigInteger, Date, Float, Index, Integer, String,
                        UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Provenance, Timestamped, UUIDPrimaryKey


class EodBar(Base, UUIDPrimaryKey, Timestamped, Provenance):
    """One scrip's settled session from the exchange bhavcopy."""

    __tablename__ = "eod_bars"
    __table_args__ = (
        UniqueConstraint("symbol", "exchange", "session_date",
                         name="uq_eod_symbol_exchange_date"),
        Index("ix_eod_session", "session_date"),
    )

    symbol: Mapped[str] = mapped_column(String(60), index=True, nullable=False)
    exchange: Mapped[str] = mapped_column(String(10), nullable=False, default="NSE")
    series: Mapped[Optional[str]] = mapped_column(String(10))
    isin: Mapped[Optional[str]] = mapped_column(String(20), index=True)
    session_date: Mapped[date] = mapped_column(Date, nullable=False)

    open: Mapped[Optional[float]] = mapped_column(Float)
    high: Mapped[Optional[float]] = mapped_column(Float)
    low: Mapped[Optional[float]] = mapped_column(Float)
    close: Mapped[Optional[float]] = mapped_column(Float)
    previous_close: Mapped[Optional[float]] = mapped_column(Float)
    vwap: Mapped[Optional[float]] = mapped_column(Float)
    volume: Mapped[Optional[int]] = mapped_column(BigInteger)
    turnover: Mapped[Optional[float]] = mapped_column(Float)
    trades: Mapped[Optional[int]] = mapped_column(BigInteger)
    settlement_price: Mapped[Optional[float]] = mapped_column(Float)

    change_pct: Mapped[Optional[float]] = mapped_column(Float)


class DeliveryRecord(Base, UUIDPrimaryKey, Timestamped, Provenance):
    """Securities-wise delivery position for one scrip and session."""

    __tablename__ = "delivery_records"
    __table_args__ = (
        UniqueConstraint("symbol", "series", "session_date",
                         name="uq_delivery_symbol_series_date"),
        Index("ix_delivery_session", "session_date"),
    )

    symbol: Mapped[str] = mapped_column(String(60), index=True, nullable=False)
    series: Mapped[str] = mapped_column(String(10), nullable=False, default="EQ")
    session_date: Mapped[date] = mapped_column(Date, nullable=False)

    traded_quantity: Mapped[Optional[int]] = mapped_column(BigInteger)
    deliverable_quantity: Mapped[Optional[int]] = mapped_column(BigInteger)
    delivery_pct: Mapped[Optional[float]] = mapped_column(Float)


class DealRecord(Base, UUIDPrimaryKey, Timestamped, Provenance):
    """A disclosed bulk or block deal.

    Not unique-constrained on client name: the same client can legitimately
    transact the same symbol twice in a session at different prices, and
    collapsing those would understate the flow.
    """

    __tablename__ = "deal_records"
    __table_args__ = (
        Index("ix_deal_symbol_date", "symbol", "session_date"),
    )

    symbol: Mapped[str] = mapped_column(String(60), index=True, nullable=False)
    security_name: Mapped[Optional[str]] = mapped_column(String(200))
    session_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    deal_type: Mapped[str] = mapped_column(String(10), nullable=False)   # BULK|BLOCK
    client_name: Mapped[Optional[str]] = mapped_column(String(240))
    buy_sell: Mapped[Optional[str]] = mapped_column(String(6))
    quantity: Mapped[Optional[int]] = mapped_column(BigInteger)
    price: Mapped[Optional[float]] = mapped_column(Float)
    value: Mapped[Optional[float]] = mapped_column(Float)
    remarks: Mapped[Optional[str]] = mapped_column(String(240))


class IngestionRun(Base, UUIDPrimaryKey, Timestamped):
    """Audit trail for every EOD ingestion.

    Without this there is no way to tell "the exchange published nothing that
    day" from "our job never ran", and those demand opposite responses.
    """

    __tablename__ = "ingestion_runs"
    __table_args__ = (
        Index("ix_ingestion_dataset_date", "dataset", "session_date"),
    )

    dataset: Mapped[str] = mapped_column(String(40), nullable=False)
    session_date: Mapped[Optional[date]] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # OK|EMPTY|FAILED
    rows_written: Mapped[int] = mapped_column(Integer, default=0)
    rows_seen: Mapped[int] = mapped_column(Integer, default=0)
    provider: Mapped[Optional[str]] = mapped_column(String(40))
    duration_ms: Mapped[Optional[float]] = mapped_column(Float)
    message: Mapped[Optional[str]] = mapped_column(String(500))
