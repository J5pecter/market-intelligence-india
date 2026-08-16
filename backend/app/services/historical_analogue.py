"""Historical analogue engine.

Finds past bars whose measurable configuration resembled today's, then reports
what happened *next* in that sample. It is a description of a sample, not a
forecast, and the payload says so in three places because the temptation to
read it as a probability is strong.

Guarantees against look-ahead bias:
  * The feature vector for bar *i* uses only data available up to and including
    bar *i*.
  * Forward returns are measured from bar i+1's open-equivalent (we use close
    to close from i to i+h, so entry is never at a price unknown at i).
  * Candidates within `horizon` bars of the end of the series are excluded,
    because their outcome has not happened yet.
  * The current bar itself and its immediate neighbours are excluded so the
    sample is not dominated by autocorrelated copies of today.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

# Features compared, with the tolerance that counts as "similar".
FEATURE_SPEC: Dict[str, Dict[str, Any]] = {
    "rsi_14": {"label": "RSI(14)", "tolerance": 7.0, "weight": 1.4},
    "pct_from_52w_high": {"label": "Distance from 52-week high",
                          "tolerance": 6.0, "weight": 1.2},
    "atr_pct": {"label": "ATR as % of price", "tolerance": 0.8, "weight": 1.0},
    "volume_ratio_20": {"label": "Volume vs 20-day average",
                        "tolerance": 0.45, "weight": 1.0},
    "adx_14": {"label": "ADX(14)", "tolerance": 8.0, "weight": 1.0},
    "ma_position": {"label": "Position vs 50-DMA (%)", "tolerance": 4.0,
                    "weight": 1.2},
}


@dataclass
class AnalogueMatch:
    date: str
    similarity: float
    features: Dict[str, Optional[float]]
    forward_return_pct: Optional[float]
    max_favourable_pct: Optional[float]
    max_adverse_pct: Optional[float]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AnalogueResult:
    horizon_bars: int
    matches_found: int
    candidates_considered: int
    sample_sufficient: bool
    statistics: Dict[str, Any] = field(default_factory=dict)
    matches: List[AnalogueMatch] = field(default_factory=list)
    current_features: Dict[str, Optional[float]] = field(default_factory=dict)
    limitations: List[str] = field(default_factory=list)
    explanation: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "horizon_bars": self.horizon_bars,
            "matches_found": self.matches_found,
            "candidates_considered": self.candidates_considered,
            "sample_sufficient": self.sample_sufficient,
            "statistics": self.statistics,
            "matches": [m.to_dict() for m in self.matches],
            "current_features": self.current_features,
            "limitations": self.limitations,
            "explanation": self.explanation,
            "methodology": "/methodology#historical-analogues",
            "disclaimer": (
                "This is a description of what happened after similar past "
                "configurations in this instrument's own history. It is not a "
                "prediction and carries no probability for the current setup."
            ),
        }


# Below this many matches the sample is too small to describe meaningfully.
MIN_SAMPLE = 8

# ...and matches drawn from a tiny candidate pool are not a sample either. With
# only a handful of eligible bars every one of them can "match" simply because
# there was nothing else to compare against, and their forward windows overlap
# almost completely. Require a pool large enough for the threshold to mean
# something.
MIN_CANDIDATE_POOL = 150


class HistoricalAnalogueService:

    def find(
        self,
        enriched: pd.DataFrame,
        horizon_bars: int = 10,
        max_matches: int = 40,
        min_similarity: float = 0.55,
        exclusion_window: int = 5,
    ) -> AnalogueResult:
        """`enriched` must be the output of indicators.compute_all."""
        limitations: List[str] = []

        if len(enriched) < horizon_bars + 60:
            return AnalogueResult(
                horizon_bars=horizon_bars, matches_found=0,
                candidates_considered=0, sample_sufficient=False,
                limitations=[
                    f"Only {len(enriched)} bars available; at least "
                    f"{horizon_bars + 60} are needed to build a sample."
                ],
                explanation=(
                    "Not enough history to look for analogues. No statistics "
                    "are produced rather than statistics from a tiny sample."
                ),
            )

        frame = enriched.copy()
        frame["ma_position"] = (
            (frame["close"] / frame["sma_50"] - 1.0) * 100.0
        )

        feature_keys = [k for k in FEATURE_SPEC if k in frame.columns]
        current = frame.iloc[-1]
        current_features = {
            k: (None if pd.isna(current.get(k)) else round(float(current[k]), 4))
            for k in feature_keys
        }

        usable = [k for k, v in current_features.items() if v is not None]
        if len(usable) < 3:
            return AnalogueResult(
                horizon_bars=horizon_bars, matches_found=0,
                candidates_considered=0, sample_sufficient=False,
                current_features=current_features,
                limitations=["Fewer than three comparable features are available today."],
                explanation="Today's configuration cannot be characterised well "
                            "enough to search for analogues.",
            )
        if len(usable) < len(feature_keys):
            limitations.append(
                "Missing today: "
                + ", ".join(FEATURE_SPEC[k]["label"] for k in feature_keys
                            if k not in usable)
                + ". Similarity was computed on the remaining features only."
            )

        # Candidates: everything with a full forward window, excluding the tail.
        last_valid = len(frame) - horizon_bars - 1
        first_valid = 60  # indicators need warm-up
        candidate_positions = [
            i for i in range(first_valid, last_valid + 1)
            if i < len(frame) - exclusion_window
        ]

        closes = frame["close"].to_numpy(dtype="float64")
        highs = frame["high"].to_numpy(dtype="float64")
        lows = frame["low"].to_numpy(dtype="float64")

        total_weight = sum(FEATURE_SPEC[k]["weight"] for k in usable)
        matches: List[AnalogueMatch] = []

        for i in candidate_positions:
            row = frame.iloc[i]
            similarity_sum = 0.0
            row_features: Dict[str, Optional[float]] = {}
            valid = True

            for key in usable:
                value = row.get(key)
                if value is None or pd.isna(value):
                    valid = False
                    break
                spec = FEATURE_SPEC[key]
                distance = abs(float(value) - current_features[key])
                # Triangular kernel: 1.0 at an exact match, 0.0 at 2x tolerance.
                closeness = max(0.0, 1.0 - distance / (2.0 * spec["tolerance"]))
                similarity_sum += closeness * spec["weight"]
                row_features[key] = round(float(value), 4)

            if not valid:
                continue
            similarity = similarity_sum / total_weight
            if similarity < min_similarity:
                continue

            entry = closes[i]
            exit_close = closes[i + horizon_bars]
            window_high = float(np.max(highs[i + 1: i + horizon_bars + 1]))
            window_low = float(np.min(lows[i + 1: i + horizon_bars + 1]))

            matches.append(AnalogueMatch(
                date=frame.index[i].date().isoformat(),
                similarity=round(similarity, 3),
                features=row_features,
                forward_return_pct=round((exit_close / entry - 1.0) * 100.0, 2),
                max_favourable_pct=round((window_high / entry - 1.0) * 100.0, 2),
                max_adverse_pct=round((window_low / entry - 1.0) * 100.0, 2),
            ))

        matches.sort(key=lambda m: m.similarity, reverse=True)
        selected = matches[:max_matches]

        # A pool this small produces matches that overlap almost completely -
        # they describe the shortage of history, not the configuration. No
        # statistics are emitted at all, because a labelled bad number still
        # gets read as a number.
        if len(candidate_positions) < MIN_CANDIDATE_POOL:
            return AnalogueResult(
                horizon_bars=horizon_bars, matches_found=len(selected),
                candidates_considered=len(candidate_positions),
                sample_sufficient=False, current_features=current_features,
                limitations=limitations + [
                    f"Only {len(candidate_positions)} bars were eligible as "
                    f"candidates, against a minimum of {MIN_CANDIDATE_POOL}.",
                    "Their forward windows overlap almost completely, so they "
                    "are not independent cases.",
                ],
                explanation=(
                    f"Not enough eligible history to build a sample: "
                    f"{len(candidate_positions)} candidate bars against a "
                    f"minimum of {MIN_CANDIDATE_POOL}. No statistics are "
                    f"produced rather than statistics that cannot be trusted."
                ),
            )

        sufficient = len(selected) >= MIN_SAMPLE

        if not selected:
            return AnalogueResult(
                horizon_bars=horizon_bars, matches_found=0,
                candidates_considered=len(candidate_positions),
                sample_sufficient=False, current_features=current_features,
                limitations=limitations + [
                    "No past bar in this instrument's history met the "
                    "similarity threshold."
                ],
                explanation=(
                    "No comparable configuration was found in the available "
                    "history, so nothing can be said about what tends to follow."
                ),
            )

        returns = np.array([m.forward_return_pct for m in selected],
                           dtype="float64")
        adverse = np.array([m.max_adverse_pct for m in selected], dtype="float64")
        favourable = np.array([m.max_favourable_pct for m in selected],
                              dtype="float64")

        wins = int((returns > 0).sum())
        statistics = {
            "sample_size": len(selected),
            "positive_cases": wins,
            "negative_cases": len(selected) - wins,
            "hit_rate_pct": round(wins / len(selected) * 100.0, 1),
            "mean_return_pct": round(float(np.mean(returns)), 2),
            "median_return_pct": round(float(np.median(returns)), 2),
            "best_return_pct": round(float(np.max(returns)), 2),
            "worst_return_pct": round(float(np.min(returns)), 2),
            "std_dev_pct": round(float(np.std(returns, ddof=1)), 2)
            if len(returns) > 1 else None,
            "mean_max_favourable_pct": round(float(np.mean(favourable)), 2),
            "mean_max_adverse_pct": round(float(np.mean(adverse)), 2),
            "worst_drawdown_in_window_pct": round(float(np.min(adverse)), 2),
            "mean_similarity": round(float(np.mean([m.similarity for m in selected])), 3),
        }

        if len(returns) > 1:
            # A crude but honest interval: mean +/- 1.96 standard errors.
            standard_error = float(np.std(returns, ddof=1)) / np.sqrt(len(returns))
            statistics["mean_return_95pct_interval"] = [
                round(float(np.mean(returns) - 1.96 * standard_error), 2),
                round(float(np.mean(returns) + 1.96 * standard_error), 2),
            ]
            statistics["interval_note"] = (
                "Interval assumes the sample is independent and roughly normal. "
                "Overlapping windows in a price series violate independence, so "
                "treat it as indicative width, not a confidence statement."
            )

        limitations.extend([
            f"Matches come only from {frame.index[0].date().isoformat()} to "
            f"{frame.index[-1].date().isoformat()} for this one instrument.",
            "Forward windows overlap, so the cases are correlated and the "
            "effective sample is smaller than the count suggests.",
            "The market regime that produced these cases may not resemble the "
            "current one.",
            "Survivorship and corporate-action effects in the price series are "
            "not separately controlled for.",
        ])
        if not sufficient:
            limitations.insert(
                0,
                f"Only {len(selected)} matches - below the {MIN_SAMPLE}-case "
                f"minimum this platform treats as describable. The statistics "
                f"below are shown for transparency, not for use.",
            )

        explanation = (
            f"{len(selected)} past configurations in this instrument's own "
            f"history resembled today's on {', '.join(FEATURE_SPEC[k]['label'] for k in usable)}. "
            f"Over the following {horizon_bars} bars those cases returned a "
            f"median {statistics['median_return_pct']}%, with "
            f"{statistics['hit_rate_pct']}% positive, a best of "
            f"{statistics['best_return_pct']}% and a worst of "
            f"{statistics['worst_return_pct']}%. Average worst drawdown inside "
            f"the window was {statistics['mean_max_adverse_pct']}%."
        )

        return AnalogueResult(
            horizon_bars=horizon_bars,
            matches_found=len(selected),
            candidates_considered=len(candidate_positions),
            sample_sufficient=sufficient,
            statistics=statistics,
            matches=selected[:20],
            current_features=current_features,
            limitations=limitations,
            explanation=explanation,
        )


historical_analogue_service = HistoricalAnalogueService()
