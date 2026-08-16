"""Cross-source reconciliation.

The problem this solves
-----------------------
Every other part of this platform asks the provider registry for a value and
takes the first provider that answers. That is the right behaviour for keeping
a screen populated, but it is the wrong behaviour for research: it means a
single vendor's bad tick silently becomes your input, and you never find out.

So this module does the opposite. It asks *every* capable source for the same
number, compares them, and refuses to collapse them into one figure unless they
actually agree. When they disagree it says so, shows every value, and names the
authoritative one - it never averages a conflict away.

Why not just always trust the highest-reliability source?
---------------------------------------------------------
Because reliability describes the source in general, not this observation.
An exchange archive is authoritative for a settled close but has nothing to say
about a price thirty seconds ago; a broker feed is authoritative intraday but
publishes no adjusted history. Agreement between two independent sources is
stronger evidence than either one's reputation, which is why the verdict is
driven by the numbers first and the hierarchy only breaks ties.

Tolerances
----------
Tolerance is expressed in percent and differs by metric because the sources
differ in kind, not just in quality. Two feeds quoting a liquid stock a few
seconds apart should agree to within a few basis points. A P/E ratio assembled
by two vendors from different trailing windows can legitimately differ by
several percent without either being wrong.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from app.core.data_quality import (DataStatus, SourceReliability, Sourced,
                                   data_quality_score)
from app.providers.base import ProviderError
from app.providers.registry import registry
from app.services.evidence import EvidenceChain, EvidenceItem, Stance

logger = logging.getLogger(__name__)


class Agreement(str, Enum):
    CONFIRMED = "CONFIRMED"                  # >=2 independent sources within tolerance
    MINOR_DIVERGENCE = "MINOR_DIVERGENCE"    # outside tolerance, within 3x of it
    CONFLICT = "CONFLICT"                    # materially different numbers
    SINGLE_SOURCE = "SINGLE_SOURCE"          # only one source could answer
    UNAVAILABLE = "UNAVAILABLE"              # nobody could answer


#: Percent disagreement tolerated before a metric stops counting as confirmed.
TOLERANCE_PCT: Dict[str, float] = {
    "ltp": 0.5,
    "close": 0.1,          # a settled close is a fact; sources must match it
    "previous_close": 0.1,
    "open": 0.25,
    "high": 0.25,
    "low": 0.25,
    "volume": 2.0,         # feeds differ on whether blocks are included
    "market_cap": 3.0,
    "pe": 5.0,
    "pb": 5.0,
    "eps_ttm": 5.0,
    "dividend_yield": 10.0,
    "delivery_pct": 0.5,
}
DEFAULT_TOLERANCE_PCT = 2.0

#: NSE and BSE are separate order books. The same stock genuinely closes at
#: different prices on each - that is a fact about the market, not a data
#: error, and flagging it as a conflict would cry wolf on every dual-listed
#: name. So a comparison that spans venues gets its own, wider tolerance.
CROSS_VENUE_TOLERANCE_PCT = 1.0

#: Which trading venue each source speaks for. Sources that report neither
#: exchange specifically (aggregators, macro publishers) are venue-neutral and
#: never trigger the cross-venue rule on their own.
_VENUE = {
    "nse_archives": "NSE",
    "bse_archives": "BSE",
    "nse": "NSE",
}

#: Which source wins when the numbers genuinely conflict. Lower sorts first.
#: This is a tie-break, not a substitute for agreement - see the module note.
_AUTHORITY = {
    "nse_archives": 0, "bse_archives": 0,     # the exchange's own settled record
    "angelone": 1, "dhan": 1, "kite": 1, "upstox": 1,   # licensed live feeds
    "nse": 2,
    "amfi": 2, "rbi": 2, "worldbank": 2,
    "yahoo": 3,                                # free aggregator
    "manual": 4,
    "demo": 9,
}


@dataclass
class SourceReading:
    """One provider's answer for one metric."""

    provider: str
    source_name: str
    value: Optional[float]
    status: DataStatus
    reliability: SourceReliability
    observed_at: Optional[datetime] = None
    age_seconds: Optional[float] = None
    error: Optional[str] = None
    deviation_pct: Optional[float] = None    # from the reference value
    is_outlier: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "source": self.source_name,
            "value": self.value,
            "status": self.status.value,
            "reliability": self.reliability.value,
            "observed_at": self.observed_at.isoformat() if self.observed_at else None,
            "age_seconds": round(self.age_seconds, 1) if self.age_seconds is not None else None,
            "error": self.error,
            "deviation_pct": self.deviation_pct,
            "is_outlier": self.is_outlier,
            "authority_rank": _AUTHORITY.get(self.provider, 5),
        }


@dataclass
class Reconciliation:
    """The verdict on one metric across every source that could answer."""

    metric: str
    agreement: Agreement
    consensus: Optional[float]           # None whenever sources conflict
    authoritative_value: Optional[float]
    authoritative_source: Optional[str]
    readings: List[SourceReading] = field(default_factory=list)
    spread_pct: Optional[float] = None
    tolerance_pct: float = DEFAULT_TOLERANCE_PCT
    explanation: str = ""
    checked_at: datetime = field(
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )

    @property
    def usable_readings(self) -> List[SourceReading]:
        return [r for r in self.readings if r.value is not None]

    @property
    def is_trustworthy(self) -> bool:
        """Safe to feed into research without a human looking first."""
        return self.agreement is Agreement.CONFIRMED

    def to_dict(self) -> Dict[str, Any]:
        return {
            "metric": self.metric,
            "agreement": self.agreement.value,
            "consensus": self.consensus,
            "authoritative_value": self.authoritative_value,
            "authoritative_source": self.authoritative_source,
            "spread_pct": self.spread_pct,
            "tolerance_pct": self.tolerance_pct,
            "source_count": len(self.usable_readings),
            "explanation": self.explanation,
            "is_trustworthy": self.is_trustworthy,
            "checked_at": self.checked_at.isoformat(),
            "readings": [r.to_dict() for r in self.readings],
        }


# --------------------------------------------------------------------------
# the comparison
# --------------------------------------------------------------------------


def reconcile(metric: str, readings: List[SourceReading],
              tolerance_pct: Optional[float] = None) -> Reconciliation:
    """Compare readings of one metric and decide whether they agree.

    The reference point is the **median**, not the mean: with three sources and
    one bad tick, the mean is dragged toward the bad value and can push two good
    sources outside tolerance, while the median simply ignores it.
    """
    usable = [r for r in readings if r.value is not None]

    venues = {_VENUE[r.provider] for r in usable if r.provider in _VENUE}
    cross_venue = len(venues) > 1
    if tolerance_pct is not None:
        tol = tolerance_pct
    else:
        tol = TOLERANCE_PCT.get(metric, DEFAULT_TOLERANCE_PCT)
        if cross_venue:
            tol = max(tol, CROSS_VENUE_TOLERANCE_PCT)

    if not usable:
        reasons = "; ".join(
            f"{r.provider}: {r.error or 'no value'}" for r in readings
        ) or "no source was asked"
        return Reconciliation(
            metric=metric, agreement=Agreement.UNAVAILABLE, consensus=None,
            authoritative_value=None, authoritative_source=None,
            readings=readings, tolerance_pct=tol,
            explanation=f"No source could supply {metric}. {reasons}",
        )

    by_authority = sorted(usable, key=lambda r: (_AUTHORITY.get(r.provider, 5),
                                                 r.age_seconds or 0))
    best = by_authority[0]

    if len(usable) == 1:
        only = usable[0]
        return Reconciliation(
            metric=metric, agreement=Agreement.SINGLE_SOURCE,
            consensus=only.value, authoritative_value=only.value,
            authoritative_source=only.provider, readings=readings,
            spread_pct=0.0, tolerance_pct=tol,
            explanation=(
                f"Only {only.source_name} could supply {metric} "
                f"({_fmt(only.value)}). Unverified - no second source was "
                "available to cross-check it against."
            ),
        )

    values = [r.value for r in usable]
    reference = statistics.median(values)
    lo, hi = min(values), max(values)
    # Guard the degenerate case: a metric that is legitimately zero (net FII
    # flow on a flat day) would make every percentage infinite.
    denom = abs(reference) if abs(reference) > 1e-9 else None
    spread_pct = round((hi - lo) / denom * 100, 4) if denom else (
        0.0 if hi == lo else None
    )

    for r in usable:
        r.deviation_pct = (
            round((r.value - reference) / denom * 100, 4) if denom
            else (0.0 if r.value == reference else None)
        )
        # An outlier is a source that departs from a majority. With only two
        # readings the median sits exactly between them, so both would always
        # be flagged - which says nothing. Two sources disagreeing is a
        # disagreement, not an outlier, and the caller has to pick.
        r.is_outlier = (
            len(usable) >= 3
            and r.deviation_pct is not None
            and abs(r.deviation_pct) > tol
        )

    if spread_pct is None:
        agreement = Agreement.CONFLICT
    elif spread_pct <= tol:
        agreement = Agreement.CONFIRMED
    elif spread_pct <= tol * 3:
        agreement = Agreement.MINOR_DIVERGENCE
    else:
        agreement = Agreement.CONFLICT

    # A consensus is published only when the sources actually agree. Handing
    # back a median of conflicting numbers would be inventing a figure that no
    # source reported - exactly the failure this module exists to prevent.
    consensus = reference if agreement is Agreement.CONFIRMED else None

    venue_note = (
        f" Readings span {' and '.join(sorted(venues))}, which are separate "
        f"order books, so the {tol}% cross-venue tolerance applies rather than "
        f"the {TOLERANCE_PCT.get(metric, DEFAULT_TOLERANCE_PCT)}% same-venue one."
        if cross_venue else ""
    )
    outliers = [r for r in usable if r.is_outlier]
    if agreement is Agreement.CONFIRMED:
        explanation = (
            f"{len(usable)} independent sources agree on {metric} within "
            f"{tol}% (spread {spread_pct}%). Consensus {_fmt(reference)} from "
            + ", ".join(f"{r.provider} {_fmt(r.value)}" for r in usable) + "."
            + venue_note
        )
    else:
        explanation = (
            f"{len(usable)} sources disagree on {metric}: "
            + ", ".join(f"{r.provider} {_fmt(r.value)}" for r in usable)
            + f". Spread {spread_pct}% exceeds the {tol}% tolerance"
            + (f"; {', '.join(r.provider for r in outliers)} "
               f"{'is' if len(outliers) == 1 else 'are'} adrift of the others"
               if outliers else
               "; with only two readings neither can be called the outlier"
               if len(usable) == 2 else "")
            + f". No consensus published. {best.source_name} is the "
            f"most authoritative source here ({_fmt(best.value)}) - treat that "
            "as the reference and check the others before relying on them."
            + venue_note
        )

    return Reconciliation(
        metric=metric, agreement=agreement, consensus=consensus,
        authoritative_value=best.value, authoritative_source=best.provider,
        readings=readings, spread_pct=spread_pct, tolerance_pct=tol,
        explanation=explanation,
    )


# --------------------------------------------------------------------------
# gathering readings
# --------------------------------------------------------------------------


def _reading_from(env: Sourced[Any], extract: Callable[[Any], Optional[float]],
                  provider: str) -> SourceReading:
    try:
        value = extract(env.value) if env.value is not None else None
    except Exception as exc:  # noqa: BLE001 - a shape mismatch is not a crash
        return SourceReading(provider=provider, source_name=env.source_name,
                             value=None, status=env.status,
                             reliability=env.reliability,
                             error=f"unexpected payload shape: {exc}")
    return SourceReading(
        provider=provider, source_name=env.source_name, value=value,
        status=env.status, reliability=env.reliability,
        observed_at=env.observed_at, age_seconds=env.age_seconds,
        error=None if value is not None else "source returned no value for this field",
    )


def gather_quote_readings(symbol: str, field_name: str = "ltp",
                          exchange: str = "NSE") -> List[SourceReading]:
    """Ask every registered quote provider for one field of one symbol.

    Deliberately bypasses the failover chain: failover stops at the first
    success, which is the opposite of what cross-checking needs.
    """
    readings: List[SourceReading] = []
    for provider in registry.all():
        if not provider.supports("quote") or not _enabled(provider.name):
            continue
        try:
            env = provider.get_quote(symbol, exchange=exchange)
        except ProviderError as exc:
            readings.append(SourceReading(
                provider=provider.name, source_name=provider.display_name,
                value=None, status=DataStatus.UNAVAILABLE,
                reliability=provider.reliability, error=str(exc)[:160],
            ))
            continue
        except Exception as exc:  # noqa: BLE001
            logger.exception("reconciliation: %s raised", provider.name)
            readings.append(SourceReading(
                provider=provider.name, source_name=provider.display_name,
                value=None, status=DataStatus.UNAVAILABLE,
                reliability=provider.reliability,
                error=f"{type(exc).__name__}: {exc}"[:160],
            ))
            continue
        readings.append(_reading_from(
            env, lambda q: getattr(q, field_name, None), provider.name
        ))
    return readings


def verify_quote(symbol: str, exchange: str = "NSE",
                 fields: Optional[List[str]] = None) -> Dict[str, Reconciliation]:
    """Cross-check the price fields of one symbol across every source."""
    fields = fields or ["ltp", "previous_close", "open", "high", "low", "volume"]
    out: Dict[str, Reconciliation] = {}
    for field_name in fields:
        out[field_name] = reconcile(
            field_name, gather_quote_readings(symbol, field_name, exchange)
        )
    return out


def verify_close_against_exchange(symbol: str,
                                  on: Optional[date] = None) -> Reconciliation:
    """Check a symbol's close against the exchange's own settled bhavcopy.

    This is the strongest check the platform can make. The bhavcopy is not
    another opinion about the close - it *is* the close, so a vendor that
    disagrees with it is simply wrong.
    """
    readings: List[SourceReading] = []

    for provider_name in ("nse_archives", "bse_archives"):
        provider = registry.get(provider_name)
        if provider is None or not _enabled(provider_name):
            continue
        try:
            env = provider.get_bhavcopy(on=on)
            row = next((r for r in (env.value or [])
                        if (r.get("symbol") or "").upper() == symbol.upper()), None)
            if row is None:
                readings.append(SourceReading(
                    provider=provider_name, source_name=provider.display_name,
                    value=None, status=env.status, reliability=provider.reliability,
                    error=f"{symbol} not listed in this bhavcopy",
                ))
                continue
            readings.append(SourceReading(
                provider=provider_name, source_name=provider.display_name,
                value=row.get("close"), status=env.status,
                reliability=provider.reliability, observed_at=env.observed_at,
                age_seconds=env.age_seconds,
            ))
        except ProviderError as exc:
            readings.append(SourceReading(
                provider=provider_name, source_name=provider.display_name,
                value=None, status=DataStatus.UNAVAILABLE,
                reliability=provider.reliability, error=str(exc)[:160],
            ))

    # Vendors' idea of the same close, for comparison.
    readings.extend(gather_quote_readings(symbol, "previous_close" if on is None
                                          else "close"))
    rec = reconcile("close", [r for r in readings if r.provider != "demo"])
    if rec.authoritative_source in ("nse_archives", "bse_archives"):
        rec.explanation += (
            " The exchange bhavcopy is the settled record, so where a vendor "
            "differs from it the vendor is wrong, not the exchange."
        )
    return rec


def _enabled(name: str) -> bool:
    from app.core.config import settings
    if name == "demo":
        return False          # a seeded row must never corroborate real data
    if name == "nse":
        return settings.enable_nse_provider
    if name.endswith("_archives"):
        return settings.enable_exchange_archives
    if name in ("angelone", "dhan", "kite", "upstox"):
        return settings.broker_is_configured(name)
    return True


# --------------------------------------------------------------------------
# evidence chain
# --------------------------------------------------------------------------


def reconciliation_evidence(recs: Dict[str, Reconciliation]) -> EvidenceChain:
    """Render reconciliation results as a standard evidence chain."""
    items: List[EvidenceItem] = []
    counter: List[EvidenceItem] = []
    limitations: List[str] = []
    gaps: List[str] = []

    for metric, rec in recs.items():
        sources = ", ".join(
            f"{r.provider}={_fmt(r.value)}" for r in rec.usable_readings
        ) or "none"
        item = EvidenceItem(
            metric=f"{metric} agreement",
            value=rec.consensus if rec.consensus is not None else rec.authoritative_value,
            stance=(Stance.POSITIVE if rec.agreement is Agreement.CONFIRMED
                    else Stance.NEGATIVE if rec.agreement is Agreement.CONFLICT
                    else Stance.NEUTRAL),
            weight=1.0,
            calculation=(
                f"median of [{sources}]; spread {rec.spread_pct}% "
                f"vs {rec.tolerance_pct}% tolerance"
            ),
            interpretation=rec.explanation,
            source=rec.authoritative_source or "none",
            observed_at=rec.checked_at,
            data_status=rec.agreement.value,
        )
        if rec.agreement in (Agreement.CONFLICT, Agreement.MINOR_DIVERGENCE):
            counter.append(item)
        else:
            items.append(item)

        if rec.agreement is Agreement.SINGLE_SOURCE:
            limitations.append(
                f"{metric} rests on one source ({rec.authoritative_source}) "
                "with nothing to cross-check it against."
            )
        elif rec.agreement is Agreement.UNAVAILABLE:
            gaps.append(f"{metric}: no source could supply a value.")
        elif rec.agreement is Agreement.CONFLICT:
            limitations.append(
                f"{metric} is disputed between sources - do not use it "
                "without deciding which source you trust and why."
            )

    # Always last, never conditional: this is the caveat that matters most,
    # and it must not be crowded out by the per-metric ones.
    limitations.append(
        "Agreement between sources means they are consistent, not that they "
        "are correct - two vendors can share an upstream feed and repeat the "
        "same error."
    )

    confirmed = sum(1 for r in recs.values() if r.is_trustworthy)
    total = len(recs) or 1
    score = round(100.0 * confirmed / total, 1)

    return EvidenceChain(
        dimension="DATA_INTEGRITY",
        score=score,
        stance=(Stance.POSITIVE if score >= 80 else
                Stance.NEUTRAL if score >= 50 else Stance.NEGATIVE),
        summary=(
            f"{confirmed} of {total} fields are corroborated by two or more "
            f"independent sources within tolerance."
            + ("" if confirmed == total else
               " The rest are single-sourced or disputed and are flagged below.")
        ),
        items=items,
        counter_items=counter,
        limitations=limitations,
        data_gaps=gaps,
        methodology_ref="/methodology#reconciliation",
    )


def _fmt(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    if abs(value) >= 1e7:
        return f"{value:,.0f}"
    return f"{value:,.2f}".rstrip("0").rstrip(".")
