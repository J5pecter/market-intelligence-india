"""Data provenance envelope.

Rule of the platform: nothing reaches the UI without a source, a timestamp and
a status. `Sourced[T]` is that envelope, and `freshness()` decides LIVE vs
DELAYED vs STALE from the age of the observation - never from wishful thinking.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, Generic, Optional, TypeVar

from app.core.market_calendar import IST, market_state, MarketStatus

T = TypeVar("T")


class DataStatus(str, Enum):
    LIVE = "LIVE"              # observed within the live threshold
    DELAYED = "DELAYED"        # provider is known-delayed (e.g. Yahoo ~15 min)
    STALE = "STALE"            # older than the acceptable window
    UNAVAILABLE = "UNAVAILABLE"
    ESTIMATED = "ESTIMATED"    # derived/modelled, not observed
    MANUAL = "MANUAL"          # entered by an admin
    UNVERIFIED = "UNVERIFIED"  # third-party aggregate, not cross-checked
    DEMO = "DEMO"              # seeded sample row


class SourceReliability(str, Enum):
    HIGH = "HIGH"        # exchange / regulator / company filing
    MEDIUM = "MEDIUM"    # licensed vendor / established publication
    LOW = "LOW"          # secondary aggregator, grey-market indicator
    UNKNOWN = "UNKNOWN"


# How old an observation may be before we stop calling it fresh, per capability.
LIVE_WINDOW_SECONDS: Dict[str, int] = {
    "quote": 120,
    "option_chain": 300,
    "index": 120,
    "news": 3600,
    "fundamentals": 60 * 60 * 24 * 45,
    "ipo_gmp": 60 * 60 * 12,
    "corporate_actions": 60 * 60 * 24,
}

STALE_WINDOW_SECONDS: Dict[str, int] = {
    "quote": 60 * 30,
    "option_chain": 60 * 30,
    "index": 60 * 30,
    "news": 60 * 60 * 24 * 3,
    "fundamentals": 60 * 60 * 24 * 200,
    "ipo_gmp": 60 * 60 * 48,
    "corporate_actions": 60 * 60 * 24 * 7,
}


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def freshness(
    observed_at: Optional[datetime],
    capability: str,
    provider_is_delayed: bool = False,
    now: Optional[datetime] = None,
) -> DataStatus:
    """Classify an observation. Market state matters: a quote from Friday's
    close is not 'stale' at 10 a.m. on Sunday - the market simply is not open.
    """
    if observed_at is None:
        return DataStatus.UNAVAILABLE
    now = _utc(now or datetime.now(tz=timezone.utc))
    age = (now - _utc(observed_at)).total_seconds()
    if age < 0:
        age = 0.0

    live_window = LIVE_WINDOW_SECONDS.get(capability, 300)
    stale_window = STALE_WINDOW_SECONDS.get(capability, 60 * 30)

    market_is_open = market_state(now.astimezone(IST)).status in (
        MarketStatus.OPEN,
        MarketStatus.PRE_OPEN,
    )
    if not market_is_open and capability in ("quote", "option_chain", "index"):
        # Outside market hours the last traded price is the correct value.
        # Only flag STALE if it predates the previous session entirely.
        return DataStatus.STALE if age > 60 * 60 * 96 else DataStatus.DELAYED

    if age > stale_window:
        return DataStatus.STALE
    if provider_is_delayed:
        return DataStatus.DELAYED
    return DataStatus.LIVE if age <= live_window else DataStatus.DELAYED


@dataclass
class Sourced(Generic[T]):
    """Value + provenance. Serialised into every API response."""

    value: T
    provider: str
    source_name: str
    status: DataStatus
    observed_at: Optional[datetime] = None
    retrieved_at: datetime = field(
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )
    reliability: SourceReliability = SourceReliability.UNKNOWN
    source_url: Optional[str] = None
    license_note: Optional[str] = None
    confidence: Optional[float] = None  # 0-100, provider-level data confidence
    notes: Optional[str] = None

    @property
    def age_seconds(self) -> Optional[float]:
        if self.observed_at is None:
            return None
        return (
            _utc(self.retrieved_at) - _utc(self.observed_at)
        ).total_seconds()

    @property
    def is_usable(self) -> bool:
        return self.status not in (DataStatus.UNAVAILABLE,)

    @property
    def is_demo(self) -> bool:
        return self.status is DataStatus.DEMO or self.provider == "demo"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "value": self.value,
            "provider": self.provider,
            "source": self.source_name,
            "status": self.status.value,
            "observed_at": self.observed_at.isoformat() if self.observed_at else None,
            "retrieved_at": self.retrieved_at.isoformat(),
            "age_seconds": self.age_seconds,
            "reliability": self.reliability.value,
            "source_url": self.source_url,
            "license_note": self.license_note,
            "confidence": self.confidence,
            "notes": self.notes,
            "is_demo": self.is_demo,
        }

    @classmethod
    def unavailable(cls, capability: str, reason: str = "no provider returned data"):
        return cls(
            value=None,
            provider="none",
            source_name=f"{capability}: unavailable",
            status=DataStatus.UNAVAILABLE,
            notes=reason,
        )


def data_quality_score(items: list[Sourced[Any]]) -> float:
    """0-100 score fed into the confidence engine.

    Weighted by status and source reliability; missing inputs score zero rather
    than being silently skipped, so a thin evidence base cannot masquerade as a
    high-quality one.
    """
    if not items:
        return 0.0
    status_weight = {
        DataStatus.LIVE: 1.0,
        DataStatus.DELAYED: 0.85,
        DataStatus.MANUAL: 0.7,
        DataStatus.ESTIMATED: 0.55,
        DataStatus.UNVERIFIED: 0.45,
        DataStatus.STALE: 0.3,
        DataStatus.DEMO: 0.2,
        DataStatus.UNAVAILABLE: 0.0,
    }
    reliability_weight = {
        SourceReliability.HIGH: 1.0,
        SourceReliability.MEDIUM: 0.85,
        SourceReliability.LOW: 0.6,
        SourceReliability.UNKNOWN: 0.5,
    }
    total = sum(
        status_weight.get(i.status, 0.0) * reliability_weight.get(i.reliability, 0.5)
        for i in items
    )
    return round(100.0 * total / len(items), 1)


def stale_banner(items: list[Sourced[Any]]) -> Optional[str]:
    """Human-readable warning the UI shows above any panel with old data."""
    bad = [i for i in items if i.status in (DataStatus.STALE, DataStatus.UNAVAILABLE)]
    if not bad:
        return None
    names = ", ".join(sorted({i.source_name for i in bad}))
    return f"Some inputs are stale or unavailable ({names}). Values shown are the last successful update, not live data."


def max_age(items: list[Sourced[Any]]) -> Optional[timedelta]:
    ages = [i.age_seconds for i in items if i.age_seconds is not None]
    return timedelta(seconds=max(ages)) if ages else None
