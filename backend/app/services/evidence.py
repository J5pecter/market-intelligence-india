"""The evidence chain.

Requirement zero of this platform: no number appears without the reader being
able to see what produced it. Every analytical service returns `EvidenceChain`
objects made of `EvidenceItem`s, and each item carries

    metric -> input -> weight -> calculation -> result -> source -> timestamp

so the UI can render a "Why?" panel mechanically, without the component
knowing anything about the domain.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class Stance(str, Enum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    NEUTRAL = "NEUTRAL"
    UNKNOWN = "UNKNOWN"


@dataclass
class EvidenceItem:
    """One inspectable fact."""

    metric: str
    value: Optional[float | str]
    stance: Stance = Stance.NEUTRAL
    weight: float = 1.0
    contribution: Optional[float] = None
    calculation: Optional[str] = None
    interpretation: Optional[str] = None
    source: Optional[str] = None
    source_url: Optional[str] = None
    observed_at: Optional[datetime] = None
    data_status: Optional[str] = None
    unit: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["stance"] = self.stance.value
        payload["observed_at"] = (
            self.observed_at.isoformat() if self.observed_at else None
        )
        return payload


@dataclass
class EvidenceChain:
    """A scored bundle of evidence about one dimension of an instrument."""

    dimension: str                      # TECHNICAL | FUNDAMENTAL | OPTIONS | ...
    score: Optional[float] = None       # 0-100
    stance: Stance = Stance.UNKNOWN
    summary: str = ""
    items: List[EvidenceItem] = field(default_factory=list)
    counter_items: List[EvidenceItem] = field(default_factory=list)
    limitations: List[str] = field(default_factory=list)
    data_gaps: List[str] = field(default_factory=list)
    methodology_ref: Optional[str] = None
    computed_at: datetime = field(
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )

    # -- building ----------------------------------------------------------

    def add(self, item: EvidenceItem) -> "EvidenceChain":
        (self.counter_items if item.stance is Stance.NEGATIVE
         else self.items).append(item)
        return self

    def note_gap(self, what: str) -> "EvidenceChain":
        self.data_gaps.append(what)
        return self

    def limit(self, what: str) -> "EvidenceChain":
        self.limitations.append(what)
        return self

    # -- scoring -----------------------------------------------------------

    def weighted_score(self, neutral_baseline: float = 50.0) -> Optional[float]:
        """Score in 0-100 from stance-weighted items.

        Each item pushes the baseline up or down in proportion to its weight.
        Items with UNKNOWN stance are excluded from the denominator so a
        missing input dilutes confidence (handled elsewhere) rather than
        silently reading as neutral agreement.
        """
        scored = [
            i for i in (self.items + self.counter_items)
            if i.stance in (Stance.POSITIVE, Stance.NEGATIVE, Stance.NEUTRAL)
        ]
        if not scored:
            return None
        total_weight = sum(abs(i.weight) for i in scored)
        if total_weight == 0:
            return None
        pull = 0.0
        for item in scored:
            direction = {
                Stance.POSITIVE: 1.0,
                Stance.NEGATIVE: -1.0,
                Stance.NEUTRAL: 0.0,
            }[item.stance]
            contribution = direction * abs(item.weight)
            item.contribution = round(contribution, 3)
            pull += contribution
        # pull/total_weight lies in [-1, 1]; map onto [0, 100] around baseline.
        normalised = pull / total_weight
        span = neutral_baseline if normalised < 0 else (100.0 - neutral_baseline)
        return round(max(0.0, min(100.0, neutral_baseline + normalised * span)), 1)

    def finalise(self, neutral_baseline: float = 50.0) -> "EvidenceChain":
        self.score = self.weighted_score(neutral_baseline)
        if self.score is None:
            self.stance = Stance.UNKNOWN
        elif self.score >= 60:
            self.stance = Stance.POSITIVE
        elif self.score <= 40:
            self.stance = Stance.NEGATIVE
        else:
            self.stance = Stance.NEUTRAL
        return self

    # -- serialisation -----------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dimension": self.dimension,
            "score": self.score,
            "stance": self.stance.value,
            "summary": self.summary,
            "evidence": [i.to_dict() for i in self.items],
            "counter_evidence": [i.to_dict() for i in self.counter_items],
            "limitations": self.limitations,
            "data_gaps": self.data_gaps,
            "methodology": self.methodology_ref,
            "computed_at": self.computed_at.isoformat(),
            "item_count": len(self.items) + len(self.counter_items),
        }

    # -- narration ---------------------------------------------------------

    def explain(self, max_items: int = 4) -> str:
        """A sentence built only from items that actually exist.

        Never emits a conclusion without naming the metrics behind it.
        """
        if self.score is None:
            return (
                f"{self.dimension.title()} view is unavailable: "
                + (", ".join(self.data_gaps) if self.data_gaps
                   else "no inputs were available.")
            )
        label = {
            Stance.POSITIVE: "constructive",
            Stance.NEGATIVE: "weak",
            Stance.NEUTRAL: "mixed",
            Stance.UNKNOWN: "unclear",
        }[self.stance]

        drivers = sorted(
            self.items + self.counter_items,
            key=lambda i: abs(i.contribution or 0.0),
            reverse=True,
        )[:max_items]
        reasons = [
            i.interpretation or f"{i.metric} = {i.value}"
            for i in drivers if i.interpretation or i.value is not None
        ]
        body = "; ".join(reasons) if reasons else "no individual driver stood out"
        text = (
            f"{self.dimension.title()} structure reads {label} "
            f"(score {self.score}/100) because {body}."
        )
        if self.data_gaps:
            text += f" Missing inputs: {', '.join(self.data_gaps)}."
        return text


def merge_stance(chains: List[EvidenceChain]) -> Dict[str, Any]:
    """Detect agreement/conflict across dimensions (requirement: conflict
    detection must be explicit, not resolved away)."""
    stances = {
        c.dimension: c.stance for c in chains if c.stance is not Stance.UNKNOWN
    }
    positives = [d for d, s in stances.items() if s is Stance.POSITIVE]
    negatives = [d for d, s in stances.items() if s is Stance.NEGATIVE]
    conflict = bool(positives) and bool(negatives)
    return {
        "conflict_detected": conflict,
        "positive_dimensions": positives,
        "negative_dimensions": negatives,
        "neutral_dimensions": [d for d, s in stances.items()
                               if s is Stance.NEUTRAL],
        "unknown_dimensions": [c.dimension for c in chains
                               if c.stance is Stance.UNKNOWN],
        "message": (
            f"Evidence conflict detected: {', '.join(positives)} read "
            f"constructive while {', '.join(negatives)} read weak."
            if conflict else
            "No direct contradiction between the dimensions that could be scored."
        ),
    }
