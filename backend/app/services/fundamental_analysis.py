"""Fundamental analysis and the company quality score.

The quality score is deliberately boring: fixed categories, fixed weights,
every metric declared with its input, its band, its contribution and its
source. Categories sum to 100 only when every metric inside them can be
computed - otherwise the category is scored on what is available and the
shortfall is reported as coverage, never silently treated as a pass.

Category budget (from the specification):
    Business quality      20
    Financial quality     20
    Growth                15
    Profitability         15
    Balance sheet         10
    Valuation             10
    Governance / risk     10
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from app.services.evidence import EvidenceChain, EvidenceItem, Stance

METHODOLOGY = "/methodology#fundamentals"

CATEGORY_BUDGET: Dict[str, float] = {
    "business_quality": 20.0,
    "financial_quality": 20.0,
    "growth": 15.0,
    "profitability": 15.0,
    "balance_sheet": 10.0,
    "valuation": 10.0,
    "governance_risk": 10.0,
}


@dataclass
class MetricScore:
    key: str
    label: str
    category: str
    value: Optional[float]
    unit: str
    weight: float                 # share of the category budget
    band: Optional[str]
    points: Optional[float]       # points actually awarded
    max_points: float
    calculation: str
    source: Optional[str]
    observed_at: Optional[str]
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class QualityScore:
    total: Optional[float]
    max_possible: float
    coverage_pct: float
    categories: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    metrics: List[MetricScore] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    explanation: str = ""
    computed_at: datetime = field(
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total": self.total,
            "max_possible": self.max_possible,
            "coverage_pct": self.coverage_pct,
            "categories": self.categories,
            "metrics": [m.to_dict() for m in self.metrics],
            "missing": self.missing,
            "explanation": self.explanation,
            "computed_at": self.computed_at.isoformat(),
            "methodology": METHODOLOGY,
            "caveat": (
                "A quality score describes the reported financial profile. It "
                "says nothing about price, timing, or whether the business is "
                "cheap."
            ),
        }


# Metric definitions: (key, label, category, share, unit, bands, higher_is_better)
# Bands are (upper_bound, fraction_of_points) evaluated in order.
_METRICS: List[Dict[str, Any]] = [
    # --- profitability ---------------------------------------------------
    {"key": "roe", "label": "Return on equity", "category": "profitability",
     "share": 0.4, "unit": "%", "higher_better": True,
     "bands": [(5, 0.0), (10, 0.3), (15, 0.6), (20, 0.85), (float("inf"), 1.0)],
     "calc": "PAT / average shareholders' equity"},
    {"key": "roce", "label": "Return on capital employed",
     "category": "profitability", "share": 0.35, "unit": "%",
     "higher_better": True,
     "bands": [(8, 0.0), (12, 0.3), (18, 0.65), (25, 0.9), (float("inf"), 1.0)],
     "calc": "EBIT / (total assets - current liabilities)"},
    {"key": "net_margin", "label": "Net profit margin",
     "category": "profitability", "share": 0.25, "unit": "%",
     "higher_better": True,
     "bands": [(2, 0.0), (5, 0.3), (10, 0.6), (18, 0.85), (float("inf"), 1.0)],
     "calc": "PAT / revenue"},

    # --- growth ----------------------------------------------------------
    {"key": "revenue_cagr_3y", "label": "3-year revenue CAGR",
     "category": "growth", "share": 0.5, "unit": "%", "higher_better": True,
     "bands": [(0, 0.0), (5, 0.25), (12, 0.6), (20, 0.85), (float("inf"), 1.0)],
     "calc": "(revenue_latest / revenue_3y_ago)^(1/3) - 1"},
    {"key": "pat_cagr_3y", "label": "3-year PAT CAGR", "category": "growth",
     "share": 0.5, "unit": "%", "higher_better": True,
     "bands": [(0, 0.0), (5, 0.25), (15, 0.65), (25, 0.9), (float("inf"), 1.0)],
     "calc": "(pat_latest / pat_3y_ago)^(1/3) - 1"},

    # --- balance sheet ---------------------------------------------------
    {"key": "debt_to_equity", "label": "Debt to equity",
     "category": "balance_sheet", "share": 0.55, "unit": "x",
     "higher_better": False,
     "bands": [(0.25, 1.0), (0.6, 0.8), (1.0, 0.55), (2.0, 0.25),
               (float("inf"), 0.0)],
     "calc": "total debt / shareholders' equity"},
    {"key": "interest_coverage", "label": "Interest coverage",
     "category": "balance_sheet", "share": 0.45, "unit": "x",
     "higher_better": True,
     "bands": [(1.5, 0.0), (3, 0.35), (6, 0.7), (10, 0.9), (float("inf"), 1.0)],
     "calc": "EBIT / interest expense"},

    # --- financial quality ----------------------------------------------
    {"key": "ebitda_margin", "label": "EBITDA margin",
     "category": "financial_quality", "share": 0.3, "unit": "%",
     "higher_better": True,
     "bands": [(5, 0.0), (10, 0.3), (18, 0.65), (28, 0.9), (float("inf"), 1.0)],
     "calc": "EBITDA / revenue"},
    {"key": "ocf_to_pat", "label": "Operating cash flow / PAT",
     "category": "financial_quality", "share": 0.4, "unit": "x",
     "higher_better": True,
     "bands": [(0.4, 0.0), (0.7, 0.35), (1.0, 0.75), (1.3, 1.0),
               (float("inf"), 0.9)],
     "calc": "operating cash flow / profit after tax",
     "note": "Cash conversion. Persistently below 1 means reported profit is "
             "not turning into cash - the single most useful quality check."},
    {"key": "current_ratio", "label": "Current ratio",
     "category": "financial_quality", "share": 0.3, "unit": "x",
     "higher_better": True,
     "bands": [(0.8, 0.0), (1.0, 0.35), (1.5, 0.8), (3.0, 1.0),
               (float("inf"), 0.7)],
     "calc": "current assets / current liabilities",
     "note": "Very high values can indicate idle working capital rather than "
             "strength, hence the taper above 3x."},

    # --- valuation --------------------------------------------------------
    {"key": "pe", "label": "Price to earnings", "category": "valuation",
     "share": 0.4, "unit": "x", "higher_better": False,
     "bands": [(12, 1.0), (20, 0.8), (35, 0.5), (60, 0.2), (float("inf"), 0.0)],
     "calc": "market cap / trailing twelve-month PAT",
     "note": "Scored in isolation here; the peer and historical comparison "
             "below is the more informative view."},
    {"key": "ev_ebitda", "label": "EV / EBITDA", "category": "valuation",
     "share": 0.35, "unit": "x", "higher_better": False,
     "bands": [(8, 1.0), (14, 0.75), (22, 0.45), (35, 0.15),
               (float("inf"), 0.0)],
     "calc": "enterprise value / EBITDA"},
    {"key": "dividend_yield", "label": "Dividend yield",
     "category": "valuation", "share": 0.25, "unit": "%",
     "higher_better": True,
     "bands": [(0.2, 0.1), (1.0, 0.4), (2.5, 0.8), (6.0, 1.0),
               (float("inf"), 0.6)],
     "calc": "dividend per share / price",
     "note": "A yield far above the sector norm often reflects a falling price "
             "rather than generosity, hence the taper above 6%."},

    # --- governance / risk ------------------------------------------------
    {"key": "promoter_holding", "label": "Promoter holding",
     "category": "governance_risk", "share": 0.4, "unit": "%",
     "higher_better": True,
     "bands": [(20, 0.2), (35, 0.5), (50, 0.85), (75, 1.0), (float("inf"), 0.8)],
     "calc": "promoter shareholding as reported in the latest disclosure"},
    {"key": "promoter_pledge", "label": "Promoter pledge",
     "category": "governance_risk", "share": 0.35, "unit": "%",
     "higher_better": False,
     "bands": [(0.01, 1.0), (5, 0.7), (15, 0.35), (30, 0.1),
               (float("inf"), 0.0)],
     "calc": "pledged shares as a share of promoter holding",
     "note": "Pledged promoter stock is a well-documented stress channel: a "
             "price fall can force sales that deepen the fall."},
    {"key": "institutional_holding", "label": "Institutional holding (FII+DII)",
     "category": "governance_risk", "share": 0.25, "unit": "%",
     "higher_better": True,
     "bands": [(2, 0.2), (8, 0.5), (20, 0.85), (float("inf"), 1.0)],
     "calc": "FII holding + DII holding"},

    # --- business quality -------------------------------------------------
    {"key": "revenue_stability", "label": "Revenue stability",
     "category": "business_quality", "share": 0.35, "unit": "score",
     "higher_better": True,
     "bands": [(0.3, 0.1), (0.55, 0.4), (0.75, 0.75), (float("inf"), 1.0)],
     "calc": "1 - (stdev of yearly revenue growth / mean absolute growth), "
             "clipped to 0-1"},
    {"key": "margin_stability", "label": "Margin stability",
     "category": "business_quality", "share": 0.35, "unit": "score",
     "higher_better": True,
     "bands": [(0.3, 0.1), (0.55, 0.4), (0.75, 0.75), (float("inf"), 1.0)],
     "calc": "1 - (stdev of EBITDA margin / mean EBITDA margin), clipped to 0-1"},
    {"key": "years_of_data", "label": "Years of reported history",
     "category": "business_quality", "share": 0.3, "unit": "years",
     "higher_better": True,
     "bands": [(1, 0.0), (3, 0.4), (5, 0.8), (float("inf"), 1.0)],
     "calc": "count of annual periods available"},
]


class FundamentalAnalysisService:

    def score(
        self,
        ratios: Dict[str, Optional[float]],
        statements: Optional[List[Dict[str, Any]]] = None,
        source: Optional[str] = None,
        observed_at: Optional[datetime] = None,
    ) -> QualityScore:
        derived = self._derive(ratios, statements or [])
        merged = {**ratios, **derived}

        metrics: List[MetricScore] = []
        missing: List[str] = []
        category_totals: Dict[str, Dict[str, float]] = {
            key: {"earned": 0.0, "available": 0.0, "budget": budget}
            for key, budget in CATEGORY_BUDGET.items()
        }

        observed_iso = observed_at.isoformat() if observed_at else None

        for spec in _METRICS:
            budget = CATEGORY_BUDGET[spec["category"]]
            max_points = round(budget * spec["share"], 3)
            value = merged.get(spec["key"])

            if value is None:
                missing.append(spec["label"])
                metrics.append(MetricScore(
                    key=spec["key"], label=spec["label"],
                    category=spec["category"], value=None, unit=spec["unit"],
                    weight=spec["share"], band=None, points=None,
                    max_points=max_points, calculation=spec["calc"],
                    source=source, observed_at=observed_iso,
                    note="Not available from the configured sources - excluded "
                         "from both the numerator and the denominator.",
                ))
                continue

            fraction, band_label = _band_fraction(float(value), spec["bands"])
            points = round(max_points * fraction, 3)
            category_totals[spec["category"]]["earned"] += points
            category_totals[spec["category"]]["available"] += max_points

            metrics.append(MetricScore(
                key=spec["key"], label=spec["label"], category=spec["category"],
                value=round(float(value), 4), unit=spec["unit"],
                weight=spec["share"], band=band_label, points=points,
                max_points=max_points, calculation=spec["calc"],
                source=source, observed_at=observed_iso,
                note=spec.get("note", ""),
            ))

        available_total = sum(c["available"] for c in category_totals.values())
        earned_total = sum(c["earned"] for c in category_totals.values())
        coverage = (available_total / 100.0 * 100.0) if available_total else 0.0

        # Normalise to 100 so the number means "quality of what we could see",
        # and report coverage separately so nobody mistakes it for completeness.
        total = round(earned_total / available_total * 100.0, 1) if available_total else None

        categories = {
            key: {
                "budget": data["budget"],
                "available_points": round(data["available"], 2),
                "earned_points": round(data["earned"], 2),
                "score_pct": round(data["earned"] / data["available"] * 100.0, 1)
                if data["available"] else None,
                "coverage_pct": round(data["available"] / data["budget"] * 100.0, 1),
            }
            for key, data in category_totals.items()
        }

        return QualityScore(
            total=total,
            max_possible=100.0,
            coverage_pct=round(coverage, 1),
            categories=categories,
            metrics=metrics,
            missing=missing,
            explanation=self._explain(total, coverage, categories, missing),
        )

    # ------------------------------------------------------------------

    @staticmethod
    def _derive(ratios: Dict[str, Optional[float]],
                statements: List[Dict[str, Any]]) -> Dict[str, Optional[float]]:
        """Fields the raw ratio feed does not carry."""
        out: Dict[str, Optional[float]] = {}

        fii = ratios.get("fii_holding")
        dii = ratios.get("dii_holding")
        if fii is not None or dii is not None:
            out["institutional_holding"] = (fii or 0.0) + (dii or 0.0)

        annual = sorted(
            [s for s in statements if s.get("period_type") == "ANNUAL"
             and s.get("period_end")],
            key=lambda s: str(s["period_end"]),
        )
        out["years_of_data"] = float(len(annual)) if annual else None

        revenues = [s.get("revenue") for s in annual if s.get("revenue")]
        pats = [s.get("pat") for s in annual if s.get("pat") is not None]
        margins = [
            s["ebitda"] / s["revenue"] * 100.0
            for s in annual
            if s.get("ebitda") is not None and s.get("revenue")
        ]

        if len(revenues) >= 3 and revenues[0] > 0:
            years = len(revenues) - 1
            out.setdefault(
                "revenue_cagr_3y",
                ((revenues[-1] / revenues[0]) ** (1.0 / years) - 1.0) * 100.0,
            )
        if len(pats) >= 3 and pats[0] > 0 and pats[-1] > 0:
            years = len(pats) - 1
            out.setdefault(
                "pat_cagr_3y",
                ((pats[-1] / pats[0]) ** (1.0 / years) - 1.0) * 100.0,
            )

        out["revenue_stability"] = _stability(
            [
                (revenues[i] / revenues[i - 1] - 1.0) * 100.0
                for i in range(1, len(revenues)) if revenues[i - 1]
            ]
        )
        out["margin_stability"] = _stability(margins, relative_to_mean=True)

        latest = annual[-1] if annual else None
        if latest and latest.get("operating_cash_flow") is not None \
                and latest.get("pat"):
            if latest["pat"] > 0:
                out["ocf_to_pat"] = latest["operating_cash_flow"] / latest["pat"]

        if latest and latest.get("ebit") is not None and latest.get("interest"):
            if latest["interest"] > 0:
                out.setdefault("interest_coverage",
                               latest["ebit"] / latest["interest"])

        return {k: v for k, v in out.items() if v is not None}

    @staticmethod
    def _explain(total, coverage, categories, missing) -> str:
        if total is None:
            return ("No fundamental metric could be computed from the available "
                    "sources, so no quality score is produced.")
        ranked = sorted(
            [(k, v) for k, v in categories.items() if v["score_pct"] is not None],
            key=lambda kv: kv[1]["score_pct"], reverse=True,
        )
        strongest = ranked[0] if ranked else None
        weakest = ranked[-1] if ranked else None
        parts = [f"Quality scores {total}/100 on the metrics that could be computed."]
        if strongest and weakest and strongest[0] != weakest[0]:
            parts.append(
                f"Strongest area is {strongest[0].replace('_', ' ')} "
                f"({strongest[1]['score_pct']}%), weakest is "
                f"{weakest[0].replace('_', ' ')} ({weakest[1]['score_pct']}%)."
            )
        parts.append(
            f"Coverage is {coverage:.0f}% of the full metric set."
        )
        if missing:
            shown = ", ".join(missing[:5])
            more = f" and {len(missing) - 5} more" if len(missing) > 5 else ""
            parts.append(f"Not available: {shown}{more}.")
        return " ".join(parts)

    # ------------------------------------------------------------------

    def build_evidence(
        self, quality: QualityScore, ratios: Dict[str, Optional[float]],
        peer_context: Optional[Dict[str, Any]] = None,
        source: Optional[str] = None, observed_at: Optional[datetime] = None,
    ) -> EvidenceChain:
        chain = EvidenceChain(dimension="FUNDAMENTAL",
                              methodology_ref=METHODOLOGY)
        status = "UNVERIFIED"

        headline = [
            ("roe", "Return on equity", 15.0, True, 1.4, "%"),
            ("roce", "Return on capital employed", 15.0, True, 1.3, "%"),
            ("debt_to_equity", "Debt to equity", 1.0, False, 1.3, "x"),
            ("ebitda_margin", "EBITDA margin", 15.0, True, 1.1, "%"),
            ("revenue_cagr_3y", "3-year revenue CAGR", 8.0, True, 1.2, "%"),
            ("pat_cagr_3y", "3-year PAT CAGR", 10.0, True, 1.2, "%"),
        ]
        merged = {m.key: m.value for m in quality.metrics if m.value is not None}
        merged.update({k: v for k, v in ratios.items() if v is not None})

        for key, label, threshold, higher_better, weight, unit in headline:
            value = merged.get(key)
            if value is None:
                chain.note_gap(label)
                continue
            good = value >= threshold if higher_better else value <= threshold
            chain.add(EvidenceItem(
                metric=label, value=round(float(value), 2),
                stance=Stance.POSITIVE if good else Stance.NEGATIVE,
                weight=weight, unit=unit,
                calculation=next(
                    (m.calculation for m in quality.metrics if m.key == key), ""
                ),
                interpretation=(
                    f"{label} is {value:.2f}{unit}, "
                    f"{'above' if value >= threshold else 'below'} the "
                    f"{threshold}{unit} reference used by this scorecard"
                ),
                source=source, data_status=status, observed_at=observed_at,
            ))

        if peer_context:
            self._peer_evidence(chain, peer_context, source, status, observed_at)

        chain.finalise()
        chain.summary = chain.explain()
        chain.limit(
            "Ratios reflect reported financials, which lag the business by a "
            "quarter or more and can be restated."
        )
        if quality.coverage_pct < 70:
            chain.limit(
                f"Only {quality.coverage_pct:.0f}% of the metric set could be "
                f"computed, so this reading rests on a partial picture."
            )
        return chain

    @staticmethod
    def _peer_evidence(chain, peer_context, source, status, observed_at) -> None:
        for metric, payload in peer_context.items():
            value = payload.get("value")
            median = payload.get("peer_median")
            if value is None or median is None:
                continue
            cheaper_is_better = metric in ("pe", "pb", "ev_ebitda", "ev_sales")
            premium_pct = (value / median - 1.0) * 100.0 if median else None
            if premium_pct is None:
                continue
            favourable = premium_pct < 0 if cheaper_is_better else premium_pct > 0
            chain.add(EvidenceItem(
                metric=f"{metric.upper()} vs peer median",
                value=round(premium_pct, 1), unit="%",
                stance=Stance.POSITIVE if favourable else Stance.NEGATIVE,
                weight=1.1,
                calculation=(
                    f"({value:.2f} / peer median {median:.2f} - 1) x 100 "
                    f"across {payload.get('peer_count', 0)} peers"
                ),
                interpretation=(
                    f"Trades at a {abs(premium_pct):.1f}% "
                    f"{'premium' if premium_pct > 0 else 'discount'} to the "
                    f"peer median on {metric.upper()}."
                ),
                source=source, data_status=status, observed_at=observed_at,
            ))


def _band_fraction(value: float,
                   bands: List[tuple[float, float]]) -> tuple[float, str]:
    previous = None
    for bound, fraction in bands:
        if value <= bound:
            label = (
                f"<= {bound:g}" if previous is None
                else f"{previous:g} < x <= {bound:g}"
            )
            return float(fraction), label
        previous = bound
    return float(bands[-1][1]), f"> {bands[-2][0]:g}"


def _stability(values: List[float], relative_to_mean: bool = False) -> Optional[float]:
    """1 - coefficient of variation, clipped to [0, 1]. Higher = steadier."""
    import statistics

    clean = [v for v in values if v is not None]
    if len(clean) < 3:
        return None
    mean = statistics.fmean(clean)
    if relative_to_mean:
        denominator = abs(mean)
    else:
        denominator = statistics.fmean([abs(v) for v in clean])
    if denominator == 0:
        return None
    cv = statistics.pstdev(clean) / denominator
    return max(0.0, min(1.0, 1.0 - cv))


fundamental_analysis_service = FundamentalAnalysisService()
