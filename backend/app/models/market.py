"""Quotes, OHLCV history and derived indicator snapshots."""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import BigInteger, Date, Float, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Provenance, Timestamped, UTCDateTime, UUIDPrimaryKey


class Quote(Base, UUIDPrimaryKey, Timestamped, Provenance):
    """Latest known snapshot per instrument (one row per instrument, upserted)."""

    __tablename__ = "quotes"
    __table_args__ = (
        UniqueConstraint("instrument_id", name="uq_quote_instrument"),
    )

    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("instruments.id", ondelete="CASCADE"), index=True, nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(60), index=True, nullable=False)

    ltp: Mapped[Optional[float]] = mapped_column(Float)
    open: Mapped[Optional[float]] = mapped_column(Float)
    high: Mapped[Optional[float]] = mapped_column(Float)
    low: Mapped[Optional[float]] = mapped_column(Float)
    previous_close: Mapped[Optional[float]] = mapped_column(Float)
    change: Mapped[Optional[float]] = mapped_column(Float)
    change_pct: Mapped[Optional[float]] = mapped_column(Float)
    volume: Mapped[Optional[int]] = mapped_column(BigInteger)
    average_volume_20d: Mapped[Optional[int]] = mapped_column(BigInteger)
    vwap: Mapped[Optional[float]] = mapped_column(Float)
    turnover: Mapped[Optional[float]] = mapped_column(Float)

    week52_high: Mapped[Optional[float]] = mapped_column(Float)
    week52_low: Mapped[Optional[float]] = mapped_column(Float)
    market_cap: Mapped[Optional[float]] = mapped_column(Float)

    bid: Mapped[Optional[float]] = mapped_column(Float)
    ask: Mapped[Optional[float]] = mapped_column(Float)
    bid_qty: Mapped[Optional[int]] = mapped_column(BigInteger)
    ask_qty: Mapped[Optional[int]] = mapped_column(BigInteger)
    open_interest: Mapped[Optional[int]] = mapped_column(BigInteger)
    oi_change: Mapped[Optional[int]] = mapped_column(BigInteger)

    market_status: Mapped[Optional[str]] = mapped_column(String(20))


class HistoricalPrice(Base, UUIDPrimaryKey, Timestamped, Provenance):
    """Adjusted OHLCV. `close` is corporate-action adjusted; `raw_close` is not,
    so backtests can prove which series they used."""

    __tablename__ = "historical_prices"
    __table_args__ = (
        UniqueConstraint("instrument_id", "interval", "bar_time",
                         name="uq_hist_bar"),
        Index("ix_hist_symbol_interval_time", "symbol", "interval", "bar_time"),
    )

    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("instruments.id", ondelete="CASCADE"), index=True, nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(60), nullable=False)
    interval: Mapped[str] = mapped_column(String(6), default="1d", nullable=False)
    bar_time: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)

    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    raw_close: Mapped[Optional[float]] = mapped_column(Float)
    volume: Mapped[Optional[int]] = mapped_column(BigInteger)
    adjustment_factor: Mapped[float] = mapped_column(Float, default=1.0)


class TechnicalIndicatorSnapshot(Base, UUIDPrimaryKey, Timestamped, Provenance):
    """Persisted so scanners can filter in SQL without recomputing every bar."""

    __tablename__ = "technical_indicators"
    __table_args__ = (
        UniqueConstraint("instrument_id", "interval", "as_of",
                         name="uq_indicator_snapshot"),
        Index("ix_indicator_symbol_asof", "symbol", "as_of"),
    )

    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("instruments.id", ondelete="CASCADE"), index=True, nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(60), nullable=False)
    interval: Mapped[str] = mapped_column(String(6), default="1d")
    as_of: Mapped[date] = mapped_column(Date, nullable=False)

    close: Mapped[Optional[float]] = mapped_column(Float)
    sma_20: Mapped[Optional[float]] = mapped_column(Float)
    sma_50: Mapped[Optional[float]] = mapped_column(Float)
    sma_100: Mapped[Optional[float]] = mapped_column(Float)
    sma_200: Mapped[Optional[float]] = mapped_column(Float)
    ema_9: Mapped[Optional[float]] = mapped_column(Float)
    ema_20: Mapped[Optional[float]] = mapped_column(Float)
    ema_50: Mapped[Optional[float]] = mapped_column(Float)
    rsi_14: Mapped[Optional[float]] = mapped_column(Float)
    macd: Mapped[Optional[float]] = mapped_column(Float)
    macd_signal: Mapped[Optional[float]] = mapped_column(Float)
    macd_hist: Mapped[Optional[float]] = mapped_column(Float)
    atr_14: Mapped[Optional[float]] = mapped_column(Float)
    atr_pct: Mapped[Optional[float]] = mapped_column(Float)
    adx_14: Mapped[Optional[float]] = mapped_column(Float)
    bb_upper: Mapped[Optional[float]] = mapped_column(Float)
    bb_lower: Mapped[Optional[float]] = mapped_column(Float)
    bb_width: Mapped[Optional[float]] = mapped_column(Float)
    stoch_k: Mapped[Optional[float]] = mapped_column(Float)
    stoch_d: Mapped[Optional[float]] = mapped_column(Float)
    supertrend: Mapped[Optional[float]] = mapped_column(Float)
    supertrend_dir: Mapped[Optional[int]] = mapped_column()
    vwap: Mapped[Optional[float]] = mapped_column(Float)
    volume_ratio_20d: Mapped[Optional[float]] = mapped_column(Float)
    distance_from_52w_high_pct: Mapped[Optional[float]] = mapped_column(Float)
    distance_from_52w_low_pct: Mapped[Optional[float]] = mapped_column(Float)
    trend_score: Mapped[Optional[float]] = mapped_column(Float)
    momentum_score: Mapped[Optional[float]] = mapped_column(Float)


class IndexSnapshot(Base, UUIDPrimaryKey, Timestamped, Provenance):
    """Breadth and regime for an index at a point in time."""

    __tablename__ = "index_snapshots"
    __table_args__ = (
        UniqueConstraint("index_symbol", "as_of", name="uq_index_snapshot"),
    )

    index_symbol: Mapped[str] = mapped_column(String(60), index=True, nullable=False)
    as_of: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)

    ltp: Mapped[Optional[float]] = mapped_column(Float)
    change_pct: Mapped[Optional[float]] = mapped_column(Float)
    advances: Mapped[Optional[int]] = mapped_column()
    declines: Mapped[Optional[int]] = mapped_column()
    unchanged: Mapped[Optional[int]] = mapped_column()
    new_highs: Mapped[Optional[int]] = mapped_column()
    new_lows: Mapped[Optional[int]] = mapped_column()
    pcr: Mapped[Optional[float]] = mapped_column(Float)
    max_pain: Mapped[Optional[float]] = mapped_column(Float)
    india_vix: Mapped[Optional[float]] = mapped_column(Float)
    regime: Mapped[Optional[str]] = mapped_column(String(30))
    regime_rationale: Mapped[Optional[str]] = mapped_column(String(2000))


class SectorPerformance(Base, UUIDPrimaryKey, Timestamped, Provenance):
    __tablename__ = "sector_performance"
    __table_args__ = (
        UniqueConstraint("sector", "as_of", name="uq_sector_perf"),
    )

    sector: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)
    change_pct: Mapped[Optional[float]] = mapped_column(Float)
    advancing: Mapped[Optional[int]] = mapped_column()
    declining: Mapped[Optional[int]] = mapped_column()
    constituents: Mapped[Optional[int]] = mapped_column()


class FlowSnapshot(Base, UUIDPrimaryKey, Timestamped, Provenance):
    """FII / DII cash-market flows. Only populated when a provider supplies
    them - never estimated."""

    __tablename__ = "flow_snapshots"
    __table_args__ = (UniqueConstraint("as_of", "segment", name="uq_flow"),)

    as_of: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    segment: Mapped[str] = mapped_column(String(20), default="CASH")
    fii_buy: Mapped[Optional[float]] = mapped_column(Float)
    fii_sell: Mapped[Optional[float]] = mapped_column(Float)
    fii_net: Mapped[Optional[float]] = mapped_column(Float)
    dii_buy: Mapped[Optional[float]] = mapped_column(Float)
    dii_sell: Mapped[Optional[float]] = mapped_column(Float)
    dii_net: Mapped[Optional[float]] = mapped_column(Float)
