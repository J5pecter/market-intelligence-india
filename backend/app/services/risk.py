"""Independent risk engine.

Deliberately separate from the signal engine: risk is assessed on its own
inputs and can veto or downgrade an otherwise attractive-looking setup. A
signal never gets to mark its own homework.

Every risk factor returns a 0-100 sub-score (higher = riskier) plus the reason,
and the overall rating is the weighted worst-case blend, not an average - one
severe factor should not be smoothed away by four benign ones.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class RiskRating(str, Enum):
    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    VERY_HIGH = "VERY_HIGH"
    UNKNOWN = "UNKNOWN"


@dataclass
class RiskFactor:
    key: str
    label: str
    score: Optional[float]          # 0-100, higher = riskier
    weight: float
    value: Optional[float | str]
    explanation: str
    source: Optional[str] = None
    is_blocking: bool = False       # forces at least HIGH regardless of blend

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RiskAssessment:
    rating: RiskRating
    score: Optional[float]
    factors: List[RiskFactor] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    unassessed: List[str] = field(default_factory=list)
    computed_at: datetime = field(
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )

    def explain(self) -> str:
        scored = [f for f in self.factors if f.score is not None]
        if not scored:
            return (
                "Risk could not be rated: "
                + (", ".join(self.unassessed) or "no inputs were available.")
            )
        drivers = sorted(scored, key=lambda f: f.score * f.weight, reverse=True)[:3]
        body = "; ".join(f"{f.label} ({f.score:.0f}/100)" for f in drivers)
        text = f"Risk is rated {self.rating.value.replace('_', ' ').lower()} " \
               f"(composite {self.score}/100). The largest contributors are {body}."
        if self.unassessed:
            text += f" Not assessed: {', '.join(self.unassessed)}."
        return text

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rating": self.rating.value,
            "score": self.score,
            "explanation": self.explain(),
            "factors": [f.to_dict() for f in self.factors],
            "warnings": self.warnings,
            "unassessed": self.unassessed,
            "computed_at": self.computed_at.isoformat(),
            "methodology": "/methodology#risk",
        }


class RiskService:

    def assess(
        self,
        *,
        symbol: str,
        segment: str = "EQUITY",
        atr_pct: Optional[float] = None,
        risk_reward: Optional[float] = None,
        downside_to_stop_pct: Optional[float] = None,
        average_daily_turnover: Optional[float] = None,
        average_volume: Optional[float] = None,
        bid_ask_spread_pct: Optional[float] = None,
        open_interest: Optional[int] = None,
        implied_volatility: Optional[float] = None,
        days_to_expiry: Optional[int] = None,
        theta_per_day: Optional[float] = None,
        option_premium: Optional[float] = None,
        upcoming_events: Optional[List[Dict[str, Any]]] = None,
        earnings_within_days: Optional[int] = None,
        gap_history_pct: Optional[float] = None,
        portfolio_weight_pct: Optional[float] = None,
        sector_weight_pct: Optional[float] = None,
        data_quality: Optional[float] = None,
    ) -> RiskAssessment:
        factors: List[RiskFactor] = []
        unassessed: List[str] = []
        warnings: List[str] = []

        # -- volatility ----------------------------------------------------
        if atr_pct is None:
            unassessed.append("price volatility")
        else:
            score = _band(atr_pct, [(1.5, 15), (2.5, 30), (4.0, 55), (6.0, 75)], 92)
            factors.append(RiskFactor(
                "volatility", "Price volatility (ATR %)", score, 1.4, atr_pct,
                f"Average daily range is {atr_pct:.2f}% of price. Wider ranges "
                f"mean a given stop is hit by ordinary noise more often.",
            ))

        # -- risk/reward ---------------------------------------------------
        if risk_reward is None:
            unassessed.append("risk/reward")
        else:
            if risk_reward >= 3:
                score = 15.0
            elif risk_reward >= 2:
                score = 30.0
            elif risk_reward >= 1.5:
                score = 45.0
            elif risk_reward >= 1:
                score = 65.0
            else:
                score = 88.0
            factors.append(RiskFactor(
                "risk_reward", "Risk/reward ratio", score, 1.5, risk_reward,
                f"Reward is {risk_reward:.2f}x the risk taken. Below 1.0 the "
                f"setup needs a win rate above 50% merely to break even, before "
                f"costs.",
                is_blocking=risk_reward < 1.0,
            ))

        # -- liquidity -----------------------------------------------------
        liquidity_score, liquidity_note = self._liquidity(
            average_daily_turnover, average_volume, bid_ask_spread_pct,
            open_interest, segment,
        )
        if liquidity_score is None:
            unassessed.append("liquidity")
        else:
            factors.append(RiskFactor(
                "liquidity", "Liquidity", liquidity_score, 1.5,
                average_daily_turnover or open_interest, liquidity_note,
                is_blocking=liquidity_score >= 80,
            ))
            if liquidity_score >= 80:
                warnings.append(
                    "Low liquidity: entering or exiting at the prices shown may "
                    "not be possible, and slippage can exceed the modelled edge."
                )

        # -- derivatives-specific -----------------------------------------
        if segment in ("OPTION", "FUTURE"):
            if days_to_expiry is None:
                unassessed.append("expiry risk")
            else:
                score = _band(
                    -days_to_expiry, [(-30, 20), (-14, 35), (-7, 55), (-3, 78)], 92
                )
                factors.append(RiskFactor(
                    "expiry", "Time to expiry", score, 1.3, days_to_expiry,
                    f"{days_to_expiry} days to expiry. Inside a week, decay and "
                    f"gamma dominate: the position's behaviour changes faster "
                    f"than the underlying moves.",
                    is_blocking=days_to_expiry <= 2,
                ))

            if segment == "OPTION":
                if theta_per_day is not None and option_premium:
                    daily_burn = abs(theta_per_day) / option_premium * 100.0
                    score = _band(daily_burn, [(1, 25), (3, 45), (6, 70), (10, 88)], 95)
                    factors.append(RiskFactor(
                        "theta", "Time decay", score, 1.3, round(daily_burn, 2),
                        f"Theta erodes about {daily_burn:.2f}% of the premium per "
                        f"calendar day at current levels. The underlying must move "
                        f"in your favour just to stand still.",
                    ))
                else:
                    unassessed.append("time decay")

                if implied_volatility is None:
                    unassessed.append("implied volatility")
                else:
                    score = _band(implied_volatility,
                                  [(20, 25), (35, 40), (50, 60), (70, 80)], 92)
                    factors.append(RiskFactor(
                        "iv", "Implied volatility level", score, 1.1,
                        implied_volatility,
                        f"Implied volatility is {implied_volatility:.1f}%. Buying "
                        f"premium at an elevated level means an IV contraction can "
                        f"lose money even if direction is right.",
                    ))

        # -- event risk ----------------------------------------------------
        event_score, event_note, event_list = self._event_risk(
            upcoming_events, earnings_within_days
        )
        if event_score is None:
            unassessed.append("event risk")
        else:
            factors.append(RiskFactor(
                "event", "Event risk", event_score, 1.4, len(event_list),
                event_note, is_blocking=event_score >= 80,
            ))

        # -- gap risk ------------------------------------------------------
        if gap_history_pct is None:
            unassessed.append("gap risk")
        else:
            score = _band(gap_history_pct, [(1, 20), (2, 40), (4, 65), (6, 85)], 95)
            factors.append(RiskFactor(
                "gap", "Overnight gap risk", score, 1.1, gap_history_pct,
                f"This instrument has averaged {gap_history_pct:.2f}% overnight "
                f"gaps recently. A stop cannot protect against a gap through it.",
            ))

        # -- concentration -------------------------------------------------
        if portfolio_weight_pct is not None:
            score = _band(portfolio_weight_pct, [(5, 15), (10, 35), (20, 60), (30, 85)], 95)
            factors.append(RiskFactor(
                "position_concentration", "Position concentration", score, 1.2,
                portfolio_weight_pct,
                f"This position would be {portfolio_weight_pct:.1f}% of the "
                f"portfolio. Concentration magnifies both outcomes.",
            ))
        if sector_weight_pct is not None:
            score = _band(sector_weight_pct, [(15, 15), (25, 35), (40, 65), (55, 85)], 95)
            factors.append(RiskFactor(
                "sector_concentration", "Sector concentration", score, 1.0,
                sector_weight_pct,
                f"Sector exposure would reach {sector_weight_pct:.1f}%. Sector "
                f"shocks move correlated names together.",
            ))

        # -- data quality --------------------------------------------------
        if data_quality is not None:
            score = max(0.0, 100.0 - data_quality)
            factors.append(RiskFactor(
                "data_quality", "Input data quality", score, 1.2,
                round(data_quality, 1),
                f"The inputs behind this assessment scored {data_quality:.0f}/100 "
                f"for freshness and source reliability. Weak inputs make every "
                f"other number here less trustworthy.",
                is_blocking=data_quality < 35,
            ))
        else:
            unassessed.append("data quality")

        return self._blend(factors, warnings, unassessed)

    # ------------------------------------------------------------------

    @staticmethod
    def _liquidity(turnover, volume, spread_pct, open_interest,
                   segment) -> tuple[Optional[float], str]:
        parts: List[str] = []
        scores: List[float] = []

        if segment in ("OPTION", "FUTURE"):
            if open_interest is not None:
                score = _band(-open_interest,
                              [(-100000, 20), (-25000, 40), (-5000, 65),
                               (-1000, 85)], 95)
                scores.append(score)
                parts.append(f"open interest {open_interest:,}")
            if spread_pct is not None:
                score = _band(spread_pct, [(0.5, 20), (1.5, 40), (3, 65), (6, 85)], 95)
                scores.append(score)
                parts.append(f"bid-ask spread {spread_pct:.2f}%")
        else:
            if turnover is not None:
                crore = turnover / 1e7
                score = _band(-crore, [(-50, 15), (-10, 35), (-2, 60), (-0.5, 85)], 95)
                scores.append(score)
                parts.append(f"average daily turnover about Rs {crore:.1f} crore")
            elif volume is not None:
                score = _band(-volume,
                              [(-1_000_000, 20), (-200_000, 40),
                               (-50_000, 65), (-10_000, 85)], 95)
                scores.append(score)
                parts.append(f"average volume {volume:,.0f} shares")

        if not scores:
            return None, "No liquidity input was available."
        blended = max(scores)  # the worst dimension governs tradability
        return blended, (
            "Liquidity assessed from " + ", ".join(parts) +
            ". The weakest dimension governs, because that is what constrains "
            "getting out."
        )

    @staticmethod
    def _event_risk(events: Optional[List[Dict[str, Any]]],
                    earnings_within_days: Optional[int]):
        if events is None and earnings_within_days is None:
            return None, "No event calendar was available.", []
        events = events or []
        today = date.today()
        near: List[Dict[str, Any]] = []
        for event in events:
            event_date = event.get("event_date")
            if isinstance(event_date, str):
                try:
                    event_date = date.fromisoformat(event_date[:10])
                except ValueError:
                    continue
            if not isinstance(event_date, date):
                continue
            days_away = (event_date - today).days
            if 0 <= days_away <= 14:
                near.append({**event, "days_away": days_away})

        score = 20.0
        notes: List[str] = []
        if earnings_within_days is not None and 0 <= earnings_within_days <= 10:
            score = max(score, 85.0 if earnings_within_days <= 3 else 65.0)
            notes.append(f"results are due in {earnings_within_days} days")
        high_impact = [e for e in near if str(e.get("expected_impact", "")).upper() == "HIGH"]
        if high_impact:
            score = max(score, 80.0)
            notes.append(f"{len(high_impact)} high-impact event(s) within 14 days")
        elif near:
            score = max(score, 45.0)
            notes.append(f"{len(near)} scheduled event(s) within 14 days")

        note = (
            "Event risk: " + "; ".join(notes) +
            ". Scheduled events can re-rate an instrument regardless of the "
            "technical or fundamental picture."
            if notes else
            "No scheduled high-impact events found in the next 14 days."
        )
        return score, note, near

    @staticmethod
    def _blend(factors: List[RiskFactor], warnings: List[str],
               unassessed: List[str]) -> RiskAssessment:
        scored = [f for f in factors if f.score is not None]
        if not scored:
            return RiskAssessment(RiskRating.UNKNOWN, None, factors,
                                  warnings, unassessed)

        total_weight = sum(f.weight for f in scored)
        weighted = sum(f.score * f.weight for f in scored) / total_weight
        worst = max(f.score for f in scored)

        # 70% weighted blend + 30% worst factor: a single severe risk keeps its
        # voice instead of being averaged into comfort.
        composite = round(0.7 * weighted + 0.3 * worst, 1)

        blocking = [f for f in scored if f.is_blocking]
        if composite >= 75:
            rating = RiskRating.VERY_HIGH
        elif composite >= 55:
            rating = RiskRating.HIGH
        elif composite >= 35:
            rating = RiskRating.MODERATE
        else:
            rating = RiskRating.LOW

        if blocking and rating in (RiskRating.LOW, RiskRating.MODERATE):
            rating = RiskRating.HIGH
            warnings.append(
                "Rating raised to HIGH because of: "
                + ", ".join(f.label for f in blocking)
                + ". A blend cannot average away a factor this severe."
            )

        if len(unassessed) >= 4:
            warnings.append(
                f"{len(unassessed)} risk dimensions could not be assessed, so "
                "this rating covers less ground than it appears to."
            )

        return RiskAssessment(rating, composite, factors, warnings, unassessed)


def _band(value: float, thresholds: List[tuple[float, float]],
          top_score: float) -> float:
    """Piecewise mapping: first threshold whose bound the value falls under."""
    for bound, score in thresholds:
        if value <= bound:
            return float(score)
    return float(top_score)


risk_service = RiskService()
