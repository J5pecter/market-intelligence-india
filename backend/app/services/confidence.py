"""Signal confidence and conflict detection.

Confidence is *not* a probability of profit. It answers a narrower question:
how much does the available evidence agree, and how good is the evidence?

Components (weights are data, shown in the payload):
    technical, fundamental, options, news, historical, catalyst,
    volume, data_quality

Two penalties that most scoring systems omit and that matter here:
  * conflict penalty - dimensions pointing opposite ways
  * coverage penalty - dimensions that could not be scored at all
Without them, a signal built on one lonely indicator would score as highly as
one corroborated by six.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.services.evidence import EvidenceChain, Stance, merge_stance

DEFAULT_WEIGHTS: Dict[str, float] = {
    "TECHNICAL": 1.6,
    "FUNDAMENTAL": 1.4,
    "OPTIONS": 1.2,
    "VOLUME": 1.0,
    "NEWS": 0.9,
    "HISTORICAL": 1.1,
    "CATALYST": 0.8,
}


@dataclass
class ConfidenceComponent:
    dimension: str
    score: Optional[float]
    weight: float
    stance: str
    contribution: Optional[float]
    note: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ConfidenceResult:
    overall: Optional[float]
    band: str
    components: List[ConfidenceComponent] = field(default_factory=list)
    data_quality: Optional[float] = None
    conflict: Dict[str, Any] = field(default_factory=dict)
    penalties: Dict[str, float] = field(default_factory=dict)
    coverage_pct: Optional[float] = None
    explanation: str = ""
    recommendation_state: str = "INSUFFICIENT_EVIDENCE"
    computed_at: datetime = field(
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "overall": self.overall,
            "band": self.band,
            "components": [c.to_dict() for c in self.components],
            "data_quality": self.data_quality,
            "conflict": self.conflict,
            "penalties": self.penalties,
            "coverage_pct": self.coverage_pct,
            "explanation": self.explanation,
            "state": self.recommendation_state,
            "computed_at": self.computed_at.isoformat(),
            "methodology": "/methodology#confidence",
            "caveat": (
                "Confidence measures agreement and evidence quality. It is not "
                "a probability that the setup will be profitable."
            ),
        }


class ConfidenceService:

    def score(
        self,
        chains: List[EvidenceChain],
        data_quality: Optional[float] = None,
        weights: Optional[Dict[str, float]] = None,
    ) -> ConfidenceResult:
        weights = {**DEFAULT_WEIGHTS, **(weights or {})}

        components: List[ConfidenceComponent] = []
        scored_weight = 0.0
        weighted_sum = 0.0
        expected_weight = 0.0

        seen = set()
        for chain in chains:
            dimension = chain.dimension.upper()
            seen.add(dimension)
            weight = weights.get(dimension, 1.0)
            expected_weight += weight
            if chain.score is None:
                components.append(ConfidenceComponent(
                    dimension, None, weight, chain.stance.value, None,
                    "Not scored: " + (", ".join(chain.data_gaps)
                                      or "no inputs available."),
                ))
                continue
            contribution = chain.score * weight
            weighted_sum += contribution
            scored_weight += weight
            components.append(ConfidenceComponent(
                dimension, chain.score, weight, chain.stance.value,
                round(contribution, 2),
                f"{len(chain.items) + len(chain.counter_items)} evidence items; "
                f"{len(chain.data_gaps)} gap(s).",
            ))

        # Dimensions that were never even attempted still count against coverage.
        for dimension, weight in weights.items():
            if dimension not in seen:
                expected_weight += weight
                components.append(ConfidenceComponent(
                    dimension, None, weight, Stance.UNKNOWN.value, None,
                    "Not evaluated for this instrument.",
                ))

        if scored_weight == 0:
            return ConfidenceResult(
                overall=None, band="UNAVAILABLE", components=components,
                data_quality=data_quality,
                conflict=merge_stance(chains),
                coverage_pct=0.0,
                explanation=(
                    "No dimension could be scored, so no confidence figure is "
                    "produced. This is a data problem, not a neutral verdict."
                ),
                recommendation_state="INSUFFICIENT_EVIDENCE",
            )

        base = weighted_sum / scored_weight
        coverage = scored_weight / expected_weight if expected_weight else 0.0

        conflict = merge_stance(chains)
        penalties: Dict[str, float] = {}

        # Conflict penalty scales with how evenly the disagreement splits.
        if conflict["conflict_detected"]:
            positives = len(conflict["positive_dimensions"])
            negatives = len(conflict["negative_dimensions"])
            balance = min(positives, negatives) / max(positives, negatives)
            penalties["conflict"] = round(10.0 + 12.0 * balance, 1)

        # Coverage penalty: up to 20 points for a thin evidence base.
        if coverage < 0.85:
            penalties["coverage"] = round((0.85 - coverage) * 24.0, 1)

        # Data-quality penalty.
        if data_quality is not None and data_quality < 70:
            penalties["data_quality"] = round((70.0 - data_quality) * 0.35, 1)

        overall = round(
            max(0.0, min(100.0, base - sum(penalties.values()))), 1
        )

        band = (
            "HIGH" if overall >= 75 else
            "MODERATE" if overall >= 55 else
            "LOW" if overall >= 35 else
            "VERY_LOW"
        )

        state = self._state(conflict, overall, coverage)

        return ConfidenceResult(
            overall=overall,
            band=band,
            components=components,
            data_quality=data_quality,
            conflict=conflict,
            penalties=penalties,
            coverage_pct=round(coverage * 100.0, 1),
            explanation=self._explain(base, overall, penalties, conflict,
                                      coverage, components),
            recommendation_state=state,
        )

    @staticmethod
    def _state(conflict: Dict[str, Any], overall: float,
               coverage: float) -> str:
        """What the platform is willing to say. Inventing certainty is worse
        than admitting the evidence is mixed."""
        if coverage < 0.35:
            return "INSUFFICIENT_EVIDENCE"
        if conflict["conflict_detected"]:
            return "MIXED_WAIT_FOR_CONFIRMATION"
        if overall >= 70:
            return "EVIDENCE_ALIGNED"
        if overall >= 50:
            return "EVIDENCE_LEANING"
        return "EVIDENCE_WEAK"

    @staticmethod
    def _explain(base, overall, penalties, conflict, coverage,
                 components) -> str:
        scored = [c for c in components if c.score is not None]
        top = sorted(scored, key=lambda c: (c.contribution or 0), reverse=True)[:3]
        parts = [
            f"Weighted evidence scores {base:.0f}/100 before penalties, led by "
            + ", ".join(f"{c.dimension.lower()} {c.score:.0f}" for c in top)
            + "."
        ]
        if penalties:
            detail = ", ".join(f"{k.replace('_', ' ')} -{v}" for k, v in penalties.items())
            parts.append(f"Penalties applied: {detail}.")
        if conflict["conflict_detected"]:
            parts.append(conflict["message"])
        missing = [c.dimension.lower() for c in components if c.score is None]
        if missing:
            parts.append(
                f"Coverage is {coverage * 100:.0f}% - no reading for "
                f"{', '.join(missing)}."
            )
        parts.append(f"Final confidence {overall}/100.")
        return " ".join(parts)


confidence_service = ConfidenceService()
