"""Options and futures snapshots plus computed Greeks."""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import BigInteger, Date, Float, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Provenance, Timestamped, UTCDateTime, UUIDPrimaryKey


class OptionChainSnapshot(Base, UUIDPrimaryKey, Timestamped, Provenance):
    """A whole chain fetch. Individual strikes hang off this via
    `snapshot_id` so every strike shares one timestamp and one provider."""

    __tablename__ = "option_chain_snapshots"
    __table_args__ = (
        Index("ix_ocs_underlying_time", "underlying_symbol", "captured_at"),
    )

    underlying_symbol: Mapped[str] = mapped_column(String(60), index=True,
                                                   nullable=False)
    expiry: Mapped[date] = mapped_column(Date, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    underlying_value: Mapped[Optional[float]] = mapped_column(Float)
    atm_strike: Mapped[Optional[float]] = mapped_column(Float)
    total_call_oi: Mapped[Optional[int]] = mapped_column(BigInteger)
    total_put_oi: Mapped[Optional[int]] = mapped_column(BigInteger)
    total_call_volume: Mapped[Optional[int]] = mapped_column(BigInteger)
    total_put_volume: Mapped[Optional[int]] = mapped_column(BigInteger)
    pcr_oi: Mapped[Optional[float]] = mapped_column(Float)
    pcr_volume: Mapped[Optional[float]] = mapped_column(Float)
    max_pain: Mapped[Optional[float]] = mapped_column(Float)
    strike_count: Mapped[Optional[int]] = mapped_column()


class OptionSnapshot(Base, UUIDPrimaryKey, Timestamped, Provenance):
    __tablename__ = "options_snapshots"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "strike", "option_type",
                         name="uq_option_strike"),
        Index("ix_option_underlying_expiry", "underlying_symbol", "expiry", "strike"),
    )

    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("option_chain_snapshots.id", ondelete="CASCADE"),
        index=True, nullable=False,
    )
    underlying_symbol: Mapped[str] = mapped_column(String(60), nullable=False)
    expiry: Mapped[date] = mapped_column(Date, nullable=False)
    strike: Mapped[float] = mapped_column(Float, nullable=False)
    option_type: Mapped[str] = mapped_column(String(2), nullable=False)  # CE | PE

    ltp: Mapped[Optional[float]] = mapped_column(Float)
    change: Mapped[Optional[float]] = mapped_column(Float)
    change_pct: Mapped[Optional[float]] = mapped_column(Float)
    open_interest: Mapped[Optional[int]] = mapped_column(BigInteger)
    oi_change: Mapped[Optional[int]] = mapped_column(BigInteger)
    oi_change_pct: Mapped[Optional[float]] = mapped_column(Float)
    volume: Mapped[Optional[int]] = mapped_column(BigInteger)
    implied_volatility: Mapped[Optional[float]] = mapped_column(Float)
    bid: Mapped[Optional[float]] = mapped_column(Float)
    ask: Mapped[Optional[float]] = mapped_column(Float)
    bid_qty: Mapped[Optional[int]] = mapped_column(BigInteger)
    ask_qty: Mapped[Optional[int]] = mapped_column(BigInteger)
    moneyness: Mapped[Optional[str]] = mapped_column(String(4))   # ITM|ATM|OTM
    buildup: Mapped[Optional[str]] = mapped_column(String(20))
    # LONG_BUILDUP | SHORT_BUILDUP | SHORT_COVERING | LONG_UNWINDING


class OptionGreeks(Base, UUIDPrimaryKey, Timestamped, Provenance):
    """Greeks are *computed*, never scraped. Every row records the model and
    its assumptions so the number can be reproduced."""

    __tablename__ = "option_greeks"
    __table_args__ = (
        UniqueConstraint("option_snapshot_id", name="uq_greeks_snapshot"),
    )

    option_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("options_snapshots.id", ondelete="CASCADE"), nullable=False
    )
    delta: Mapped[Optional[float]] = mapped_column(Float)
    gamma: Mapped[Optional[float]] = mapped_column(Float)
    theta: Mapped[Optional[float]] = mapped_column(Float)   # per calendar day
    vega: Mapped[Optional[float]] = mapped_column(Float)    # per 1 vol point
    rho: Mapped[Optional[float]] = mapped_column(Float)
    implied_volatility: Mapped[Optional[float]] = mapped_column(Float)
    theoretical_price: Mapped[Optional[float]] = mapped_column(Float)

    model: Mapped[str] = mapped_column(String(40), default="black_scholes_merton")
    risk_free_rate: Mapped[Optional[float]] = mapped_column(Float)
    dividend_yield: Mapped[Optional[float]] = mapped_column(Float)
    time_to_expiry_years: Mapped[Optional[float]] = mapped_column(Float)
    volatility_source: Mapped[Optional[str]] = mapped_column(String(80))
    assumption_notes: Mapped[Optional[str]] = mapped_column(Text)
    solver_converged: Mapped[bool] = mapped_column(default=True)


class FuturesSnapshot(Base, UUIDPrimaryKey, Timestamped, Provenance):
    __tablename__ = "futures_snapshots"
    __table_args__ = (
        Index("ix_fut_underlying_expiry", "underlying_symbol", "expiry",
              "captured_at"),
    )

    underlying_symbol: Mapped[str] = mapped_column(String(60), index=True,
                                                   nullable=False)
    expiry: Mapped[date] = mapped_column(Date, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)

    spot: Mapped[Optional[float]] = mapped_column(Float)
    ltp: Mapped[Optional[float]] = mapped_column(Float)
    change: Mapped[Optional[float]] = mapped_column(Float)
    change_pct: Mapped[Optional[float]] = mapped_column(Float)
    basis: Mapped[Optional[float]] = mapped_column(Float)          # futures - spot
    basis_pct: Mapped[Optional[float]] = mapped_column(Float)
    annualised_basis_pct: Mapped[Optional[float]] = mapped_column(Float)
    open_interest: Mapped[Optional[int]] = mapped_column(BigInteger)
    oi_change: Mapped[Optional[int]] = mapped_column(BigInteger)
    volume: Mapped[Optional[int]] = mapped_column(BigInteger)
    lot_size: Mapped[Optional[int]] = mapped_column()
    buildup: Mapped[Optional[str]] = mapped_column(String(20))
