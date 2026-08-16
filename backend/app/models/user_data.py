"""Watchlists, alerts, paper trading, portfolio and backtests."""

from __future__ import annotations

from datetime import date, datetime
from typing import List, Optional

from sqlalchemy import (Boolean, Date, Float, ForeignKey, Index, Integer,
                        String, Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamped, UTCDateTime, UUIDPrimaryKey


class Watchlist(Base, UUIDPrimaryKey, Timestamped):
    __tablename__ = "watchlists"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_watchlist_name"),)

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(String(400), default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    user = relationship("User", back_populates="watchlists")
    items: Mapped[List["WatchlistItem"]] = relationship(
        back_populates="watchlist", cascade="all, delete-orphan"
    )


class WatchlistItem(Base, UUIDPrimaryKey, Timestamped):
    __tablename__ = "watchlist_items"
    __table_args__ = (
        UniqueConstraint("watchlist_id", "symbol", "segment", "expiry", "strike",
                         "option_type", name="uq_watchlist_item"),
    )

    watchlist_id: Mapped[str] = mapped_column(
        ForeignKey("watchlists.id", ondelete="CASCADE"), index=True, nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(60), nullable=False)
    segment: Mapped[str] = mapped_column(String(12), default="EQUITY")
    expiry: Mapped[Optional[date]] = mapped_column(Date)
    strike: Mapped[Optional[float]] = mapped_column(Float)
    option_type: Mapped[Optional[str]] = mapped_column(String(2))
    note: Mapped[Optional[str]] = mapped_column(String(400))

    watchlist = relationship("Watchlist", back_populates="items")


class Alert(Base, UUIDPrimaryKey, Timestamped):
    """A user-defined condition. `condition_json` is evaluated by AlertService."""

    __tablename__ = "alerts"
    __table_args__ = (Index("ix_alert_user_active", "user_id", "is_active"),)

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), default="")
    symbol: Mapped[Optional[str]] = mapped_column(String(60), index=True)
    segment: Mapped[str] = mapped_column(String(12), default="EQUITY")

    alert_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # PRICE_ABOVE|PRICE_BELOW|PCT_MOVE|ENTERS_ENTRY_RANGE|TARGET_REACHED|
    # STOP_LOSS_REACHED|RSI_ABOVE|RSI_BELOW|EMA_CROSS|VOLUME_MULTIPLE|
    # OI_CHANGE|IV_CHANGE|VIX_ABOVE|NEWS_KEYWORD|NEWS_IMPACT|IPO_GMP_CHANGE|
    # IPO_SUBSCRIPTION|EARNINGS_ANNOUNCED|FII_FLOW
    condition_json: Mapped[str] = mapped_column(Text, default="{}")
    research_call_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("research_calls.id", ondelete="CASCADE")
    )
    ipo_id: Mapped[Optional[str]] = mapped_column(ForeignKey("ipos.id",
                                                             ondelete="CASCADE"))

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    trigger_once: Mapped[bool] = mapped_column(Boolean, default=True)
    cooldown_minutes: Mapped[int] = mapped_column(Integer, default=60)
    channels: Mapped[str] = mapped_column(String(200), default="in_app")
    last_triggered_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    trigger_count: Mapped[int] = mapped_column(Integer, default=0)
    last_evaluated_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    last_evaluation_note: Mapped[Optional[str]] = mapped_column(String(600))


class AlertEvent(Base, UUIDPrimaryKey, Timestamped):
    __tablename__ = "alert_events"
    __table_args__ = (Index("ix_alert_event_user_time", "user_id", "created_at"),)

    alert_id: Mapped[str] = mapped_column(
        ForeignKey("alerts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(Text, default="")
    triggered_value: Mapped[Optional[float]] = mapped_column(Float)
    evidence_json: Mapped[Optional[str]] = mapped_column(Text)
    delivery_status: Mapped[str] = mapped_column(String(200), default="in_app:PENDING")
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)


class PaperPosition(Base, UUIDPrimaryKey, Timestamped):
    """Paper trading only. Nothing here ever reaches a broker."""

    __tablename__ = "paper_positions"
    __table_args__ = (Index("ix_paper_user_status", "user_id", "status"),)

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(60), nullable=False)
    segment: Mapped[str] = mapped_column(String(12), default="EQUITY")
    expiry: Mapped[Optional[date]] = mapped_column(Date)
    strike: Mapped[Optional[float]] = mapped_column(Float)
    option_type: Mapped[Optional[str]] = mapped_column(String(2))

    side: Mapped[str] = mapped_column(String(6), default="LONG")  # LONG|SHORT
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    lot_size: Mapped[int] = mapped_column(Integer, default=1)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    entry_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    stop_loss: Mapped[Optional[float]] = mapped_column(Float)
    target: Mapped[Optional[float]] = mapped_column(Float)

    exit_price: Mapped[Optional[float]] = mapped_column(Float)
    exit_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    status: Mapped[str] = mapped_column(String(12), default="OPEN")  # OPEN|CLOSED
    realised_pnl: Mapped[Optional[float]] = mapped_column(Float)
    charges: Mapped[Optional[float]] = mapped_column(Float)
    linked_call_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("research_calls.id", ondelete="SET NULL")
    )
    note: Mapped[Optional[str]] = mapped_column(String(600))


class PortfolioHolding(Base, UUIDPrimaryKey, Timestamped):
    """Manually entered real holdings (read-only analytics, no execution)."""

    __tablename__ = "portfolio"
    __table_args__ = (
        UniqueConstraint("user_id", "symbol", "segment", name="uq_portfolio_row"),
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(60), nullable=False)
    segment: Mapped[str] = mapped_column(String(12), default="EQUITY")
    quantity: Mapped[float] = mapped_column(Float, default=0.0)
    average_cost: Mapped[float] = mapped_column(Float, default=0.0)
    sector: Mapped[Optional[str]] = mapped_column(String(160))
    note: Mapped[Optional[str]] = mapped_column(String(400))


class PortfolioTransaction(Base, UUIDPrimaryKey, Timestamped):
    __tablename__ = "portfolio_transactions"
    __table_args__ = (Index("ix_txn_user_date", "user_id", "traded_on"),)

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(60), nullable=False)
    segment: Mapped[str] = mapped_column(String(12), default="EQUITY")
    txn_type: Mapped[str] = mapped_column(String(12), nullable=False)
    # BUY|SELL|DIVIDEND|BONUS|SPLIT|CHARGE
    quantity: Mapped[float] = mapped_column(Float, default=0.0)
    price: Mapped[float] = mapped_column(Float, default=0.0)
    amount: Mapped[Optional[float]] = mapped_column(Float)
    charges: Mapped[float] = mapped_column(Float, default=0.0)
    traded_on: Mapped[date] = mapped_column(Date, nullable=False)
    note: Mapped[Optional[str]] = mapped_column(String(400))


class Backtest(Base, UUIDPrimaryKey, Timestamped):
    __tablename__ = "backtests"

    user_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    name: Mapped[str] = mapped_column(String(200), default="")
    strategy_json: Mapped[str] = mapped_column(Text, nullable=False)
    universe_json: Mapped[str] = mapped_column(Text, default="[]")
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    in_sample_end: Mapped[Optional[date]] = mapped_column(Date)
    interval: Mapped[str] = mapped_column(String(6), default="1d")

    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    error: Mapped[Optional[str]] = mapped_column(Text)

    metrics_json: Mapped[Optional[str]] = mapped_column(Text)
    in_sample_metrics_json: Mapped[Optional[str]] = mapped_column(Text)
    out_of_sample_metrics_json: Mapped[Optional[str]] = mapped_column(Text)
    walk_forward_json: Mapped[Optional[str]] = mapped_column(Text)
    equity_curve_json: Mapped[Optional[str]] = mapped_column(Text)
    assumptions_json: Mapped[Optional[str]] = mapped_column(Text)
    bars_used: Mapped[Optional[int]] = mapped_column(Integer)
    data_warnings: Mapped[Optional[str]] = mapped_column(Text)


class BacktestTrade(Base, UUIDPrimaryKey, Timestamped):
    __tablename__ = "backtest_trades"
    __table_args__ = (Index("ix_bt_trade_backtest", "backtest_id"),)

    backtest_id: Mapped[str] = mapped_column(
        ForeignKey("backtests.id", ondelete="CASCADE"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(60), nullable=False)
    direction: Mapped[str] = mapped_column(String(6), default="LONG")
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    exit_date: Mapped[Optional[date]] = mapped_column(Date)
    exit_price: Mapped[Optional[float]] = mapped_column(Float)
    exit_reason: Mapped[Optional[str]] = mapped_column(String(40))
    quantity: Mapped[float] = mapped_column(Float, default=1.0)
    gross_pnl: Mapped[Optional[float]] = mapped_column(Float)
    costs: Mapped[Optional[float]] = mapped_column(Float)
    net_pnl: Mapped[Optional[float]] = mapped_column(Float)
    return_pct: Mapped[Optional[float]] = mapped_column(Float)
    holding_days: Mapped[Optional[int]] = mapped_column(Integer)
    sample: Mapped[str] = mapped_column(String(4), default="IS")  # IS | OOS
