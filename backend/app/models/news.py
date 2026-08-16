"""News articles, their scoring and the price reaction around them."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, Float, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Provenance, Timestamped, UTCDateTime, UUIDPrimaryKey


class NewsArticle(Base, UUIDPrimaryKey, Timestamped, Provenance):
    __tablename__ = "news"
    __table_args__ = (
        UniqueConstraint("url_hash", name="uq_news_url"),
        Index("ix_news_symbol_published", "primary_symbol", "published_at"),
    )

    headline: Mapped[str] = mapped_column(String(600), nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(Text)
    url: Mapped[str] = mapped_column(String(1200), nullable=False)
    url_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    publisher: Mapped[str] = mapped_column(String(200), default="")
    published_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, index=True)

    primary_symbol: Mapped[Optional[str]] = mapped_column(String(60), index=True)
    related_symbols: Mapped[Optional[str]] = mapped_column(Text)  # JSON list
    sector: Mapped[Optional[str]] = mapped_column(String(160))

    event_category: Mapped[Optional[str]] = mapped_column(String(40))
    # EARNINGS|MANAGEMENT|REGULATORY|GOVERNMENT|ORDER_WIN|ORDER_LOSS|ACQUISITION|
    # MERGER|FUNDRAISING|INSIDER|PROMOTER|DIVIDEND|BUYBACK|RATING|LITIGATION|
    # GOVERNANCE|PRODUCT|CAPEX|MACRO|SECTOR|OTHER

    is_reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    is_suppressed: Mapped[bool] = mapped_column(Boolean, default=False)


class NewsScore(Base, UUIDPrimaryKey, Timestamped):
    """Financial-context scoring. Kept apart from the article so the model can
    be re-run and versioned without rewriting source records."""

    __tablename__ = "news_sentiment"
    __table_args__ = (
        UniqueConstraint("article_id", "model_version", name="uq_news_score"),
    )

    article_id: Mapped[str] = mapped_column(
        ForeignKey("news.id", ondelete="CASCADE"), index=True, nullable=False
    )
    model_version: Mapped[str] = mapped_column(String(20), default="1.0.0")

    sentiment: Mapped[str] = mapped_column(String(10), default="NEUTRAL")
    sentiment_score: Mapped[float] = mapped_column(Float, default=0.0)  # -1..+1

    headline_sentiment: Mapped[float] = mapped_column(Float, default=0.0)
    event_importance: Mapped[float] = mapped_column(Float, default=0.0)   # 0..1
    historical_reaction: Mapped[Optional[float]] = mapped_column(Float)   # 0..1
    sector_relevance: Mapped[float] = mapped_column(Float, default=0.0)
    company_relevance: Mapped[float] = mapped_column(Float, default=0.0)
    source_credibility: Mapped[float] = mapped_column(Float, default=0.5)

    impact_score: Mapped[float] = mapped_column(Float, default=0.0)  # 0..100
    explanation: Mapped[Optional[str]] = mapped_column(Text)          # JSON breakdown
    matched_terms: Mapped[Optional[str]] = mapped_column(Text)        # JSON list


class NewsPriceReaction(Base, UUIDPrimaryKey, Timestamped, Provenance):
    """Did the market actually care? Filled in by a follow-up job."""

    __tablename__ = "news_price_reactions"
    __table_args__ = (UniqueConstraint("article_id", name="uq_news_reaction"),)

    article_id: Mapped[str] = mapped_column(
        ForeignKey("news.id", ondelete="CASCADE"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(60), index=True, nullable=False)
    price_before: Mapped[Optional[float]] = mapped_column(Float)
    price_5m: Mapped[Optional[float]] = mapped_column(Float)
    price_15m: Mapped[Optional[float]] = mapped_column(Float)
    price_1h: Mapped[Optional[float]] = mapped_column(Float)
    price_eod: Mapped[Optional[float]] = mapped_column(Float)
    return_eod_pct: Mapped[Optional[float]] = mapped_column(Float)
    volume_ratio: Mapped[Optional[float]] = mapped_column(Float)
    resolution_note: Mapped[Optional[str]] = mapped_column(String(400))
