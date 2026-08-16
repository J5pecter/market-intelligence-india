"""IPO research scoring, valuation, SWOT and the GMP simulator.

Deliberately *not* a SUBSCRIBE/AVOID button. The output is six component
scores plus a descriptive label, and every component reports its own coverage
so a confident-looking number built on two data points cannot hide.

Grey Market Premium is treated throughout as what it is: an unofficial,
unregulated quote from private dealers. It contributes at most 15 points to
the overall score and never drives the label on its own.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

METHODOLOGY = "/methodology#ipo"

COMPONENT_WEIGHTS: Dict[str, float] = {
    "business_quality": 0.22,
    "financial_quality": 0.24,
    "valuation_attractiveness": 0.22,
    "subscription_strength": 0.12,
    "gmp_signal": 0.08,
    "risk": 0.12,          # inverted before blending: high risk lowers the score
}


@dataclass
class ComponentScore:
    key: str
    label: str
    score: Optional[float]
    coverage_pct: float
    inputs: Dict[str, Any] = field(default_factory=dict)
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class IpoAssessment:
    overall_score: Optional[float]
    label: str
    label_reason: str
    components: List[ComponentScore] = field(default_factory=list)
    valuation: Dict[str, Any] = field(default_factory=dict)
    swot: Dict[str, List[Dict[str, str]]] = field(default_factory=dict)
    data_completeness_pct: float = 0.0
    limitations: List[str] = field(default_factory=list)
    computed_at: datetime = field(
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "overall_score": self.overall_score,
            "label": self.label,
            "label_reason": self.label_reason,
            "components": [c.to_dict() for c in self.components],
            "valuation": self.valuation,
            "swot": self.swot,
            "data_completeness_pct": self.data_completeness_pct,
            "limitations": self.limitations,
            "computed_at": self.computed_at.isoformat(),
            "methodology": METHODOLOGY,
            "disclaimer": (
                "A research score is not an application recommendation. It "
                "describes the profile visible in the offer document and the "
                "public data available at the time it was computed."
            ),
        }


class IpoAnalysisService:

    def assess(
        self,
        ipo: Dict[str, Any],
        financials: List[Dict[str, Any]],
        risk_factors: List[Dict[str, Any]],
        latest_gmp: Optional[Dict[str, Any]] = None,
        gmp_history: Optional[List[Dict[str, Any]]] = None,
        subscription: Optional[Dict[str, Any]] = None,
        peers: Optional[List[Dict[str, Any]]] = None,
    ) -> IpoAssessment:
        components = [
            self._business_quality(ipo, financials, risk_factors),
            self._financial_quality(financials),
            self._valuation(ipo, financials, peers),
            self._subscription_strength(subscription, ipo),
            self._gmp_signal(latest_gmp, gmp_history, ipo),
            self._risk(risk_factors, ipo, financials),
        ]

        valuation = self._valuation_detail(ipo, financials, peers)
        swot = self._swot(ipo, financials, risk_factors, valuation, subscription)

        overall, completeness = self._blend(components)
        label, reason = self._label(overall, components, completeness)

        limitations = [
            "Every figure here comes from the offer document and public "
            "disclosures as entered into this system. Nothing is verified "
            "independently.",
            "Pre-IPO financials are restated for the offer and are not "
            "directly comparable with a listed peer's reported numbers.",
            "Peer multiples are point-in-time and move with the market.",
        ]
        if not financials:
            limitations.insert(0, "No financial history was available at all.")
        if latest_gmp:
            limitations.append(
                "Grey Market Premium is an unofficial dealer quote, not an "
                "exchange price. It can change or vanish without notice."
            )

        return IpoAssessment(
            overall_score=overall,
            label=label,
            label_reason=reason,
            components=components,
            valuation=valuation,
            swot=swot,
            data_completeness_pct=completeness,
            limitations=limitations,
        )

    # ------------------------------------------------------------------
    # components
    # ------------------------------------------------------------------

    def _business_quality(self, ipo, financials, risk_factors) -> ComponentScore:
        reasons: List[str] = []
        parts: List[float] = []
        inputs: Dict[str, Any] = {}
        expected = 4

        years = len(financials)
        inputs["years_of_financials"] = years
        if years:
            score = min(100.0, 25.0 * years)
            parts.append(score)
            reasons.append(
                f"{years} reported period(s) disclosed - "
                f"{'a usable track record' if years >= 3 else 'a short record'}."
            )

        revenues = [f.get("revenue") for f in financials if f.get("revenue")]
        if len(revenues) >= 2:
            growing = sum(
                1 for i in range(1, len(revenues)) if revenues[i] > revenues[i - 1]
            )
            score = growing / (len(revenues) - 1) * 100.0
            parts.append(score)
            inputs["revenue_growth_years"] = f"{growing}/{len(revenues) - 1}"
            reasons.append(
                f"Revenue grew in {growing} of the last {len(revenues) - 1} "
                f"comparisons."
            )

        concentration = [
            r for r in risk_factors
            if r.get("category") in ("CUSTOMER_CONCENTRATION",
                                     "SUPPLIER_CONCENTRATION")
        ]
        if concentration:
            worst = max(
                (r.get("quantum") or 0) for r in concentration
            )
            inputs["max_concentration_pct"] = worst
            score = max(0.0, 100.0 - worst * 1.2)
            parts.append(score)
            reasons.append(
                f"Disclosed concentration reaches {worst:.0f}% - a single "
                f"relationship carries that much of the business."
            )
        elif risk_factors:
            parts.append(65.0)
            inputs["max_concentration_pct"] = None
            reasons.append("No customer/supplier concentration risk was recorded.")

        if ipo.get("industry"):
            parts.append(60.0)
            inputs["industry"] = ipo["industry"]
            reasons.append(f"Industry classified as {ipo['industry']}.")

        return self._pack("business_quality", "Business quality", parts,
                          expected, inputs, reasons)

    def _financial_quality(self, financials) -> ComponentScore:
        reasons: List[str] = []
        parts: List[float] = []
        inputs: Dict[str, Any] = {}
        expected = 5

        if not financials:
            return ComponentScore("financial_quality", "Financial quality",
                                  None, 0.0, inputs,
                                  ["No financial data was available."])

        latest = financials[-1]
        for key, label, bands in (
            ("ebitda_margin", "EBITDA margin",
             [(0, 0), (5, 25), (12, 55), (20, 80), (999, 95)]),
            ("net_margin", "Net margin",
             [(0, 0), (3, 30), (8, 60), (15, 85), (999, 95)]),
            ("roe", "Return on equity",
             [(0, 0), (8, 30), (15, 65), (22, 88), (999, 95)]),
            ("roce", "Return on capital employed",
             [(0, 0), (10, 30), (16, 65), (24, 88), (999, 95)]),
        ):
            value = latest.get(key)
            if value is None:
                continue
            score = _band(float(value), bands)
            parts.append(score)
            inputs[key] = value
            reasons.append(f"{label} of {value:.1f}% scores {score:.0f}/100.")

        debt = latest.get("total_debt")
        equity = latest.get("net_worth")
        if debt is not None and equity:
            ratio = debt / equity
            score = _band(-ratio, [(-2, 5), (-1, 30), (-0.5, 65), (-0.2, 85),
                                   (999, 95)])
            parts.append(score)
            inputs["debt_to_equity"] = round(ratio, 2)
            reasons.append(
                f"Debt/equity of {ratio:.2f}x scores {score:.0f}/100."
            )

        return self._pack("financial_quality", "Financial quality", parts,
                          expected, inputs, reasons)

    def _valuation(self, ipo, financials, peers) -> ComponentScore:
        detail = self._valuation_detail(ipo, financials, peers)
        inputs = {
            "implied_pe": detail.get("implied_pe"),
            "peer_median_pe": detail.get("peer_median_pe"),
            "premium_pct": detail.get("premium_to_peer_pct"),
        }
        reasons: List[str] = []
        parts: List[float] = []
        expected = 2

        implied_pe = detail.get("implied_pe")
        if implied_pe is not None:
            score = _band(-implied_pe, [(-60, 10), (-40, 30), (-25, 60),
                                        (-15, 85), (999, 95)])
            parts.append(score)
            reasons.append(
                f"At the upper price band the issue is offered at "
                f"{implied_pe:.1f}x trailing earnings."
            )

        premium = detail.get("premium_to_peer_pct")
        if premium is not None:
            score = _band(-premium, [(-50, 8), (-20, 30), (0, 60), (20, 85),
                                     (999, 92)])
            parts.append(score)
            reasons.append(
                f"That is a {abs(premium):.0f}% "
                f"{'premium to' if premium > 0 else 'discount to'} the peer "
                f"median."
            )
        elif implied_pe is not None:
            reasons.append(
                "No comparable peer multiples were supplied, so the valuation "
                "is scored in isolation - a materially weaker test."
            )

        return self._pack("valuation_attractiveness", "Valuation attractiveness",
                          parts, expected, inputs, reasons)

    def _subscription_strength(self, subscription, ipo) -> ComponentScore:
        inputs: Dict[str, Any] = {}
        reasons: List[str] = []
        parts: List[float] = []
        expected = 3

        if not subscription:
            return ComponentScore(
                "subscription_strength", "Subscription strength", None, 0.0,
                inputs,
                ["The issue has not opened, or no subscription data was recorded."],
            )

        for key, label, weight_note in (
            ("qib_times", "QIB", "institutional demand is the most informative leg"),
            ("nii_times", "NII", "often funded and can unwind after listing"),
            ("retail_times", "Retail", "sentiment-driven"),
        ):
            value = subscription.get(key)
            if value is None:
                continue
            score = _band(value, [(0.5, 5), (1, 25), (3, 55), (10, 80),
                                  (50, 93), (9999, 98)])
            parts.append(score)
            inputs[key] = value
            reasons.append(
                f"{label} subscribed {value:.2f}x ({weight_note}) - "
                f"scores {score:.0f}/100."
            )

        total = subscription.get("total_times")
        if total is not None:
            inputs["total_times"] = total
            if total < 1:
                reasons.append(
                    f"The issue is under-subscribed overall at {total:.2f}x."
                )

        return self._pack("subscription_strength", "Subscription strength",
                          parts, expected, inputs, reasons)

    def _gmp_signal(self, latest_gmp, gmp_history, ipo) -> ComponentScore:
        inputs: Dict[str, Any] = {}
        reasons: List[str] = []
        parts: List[float] = []
        expected = 2

        if not latest_gmp or latest_gmp.get("gmp") is None:
            return ComponentScore(
                "gmp_signal", "Grey market signal", None, 0.0, inputs,
                ["No grey market quote was recorded for this issue."],
            )

        band_high = ipo.get("price_band_high")
        gmp = latest_gmp["gmp"]
        gmp_pct = latest_gmp.get("gmp_pct")
        if gmp_pct is None and band_high:
            gmp_pct = gmp / band_high * 100.0
        inputs["gmp"] = gmp
        inputs["gmp_pct"] = round(gmp_pct, 2) if gmp_pct is not None else None
        inputs["observed_on"] = latest_gmp.get("observed_on")

        if gmp_pct is not None:
            score = _band(gmp_pct, [(-5, 5), (0, 25), (10, 55), (25, 78),
                                    (50, 90), (9999, 92)])
            parts.append(score)
            reasons.append(
                f"The quoted premium is Rs {gmp:g} "
                f"({gmp_pct:.1f}% of the upper band)."
            )

        if gmp_history and len(gmp_history) >= 3:
            values = [
                h.get("gmp") for h in gmp_history[-5:] if h.get("gmp") is not None
            ]
            if len(values) >= 3:
                trend = values[-1] - values[0]
                score = _band(trend, [(-20, 15), (-5, 35), (0, 50), (10, 75),
                                      (9999, 88)])
                parts.append(score)
                inputs["gmp_trend"] = round(trend, 2)
                reasons.append(
                    f"Over the last {len(values)} observations the quote has "
                    f"moved {trend:+.1f}, which matters more than its level."
                )
        else:
            reasons.append(
                "Fewer than three observations - the trend, which is the more "
                "useful part, cannot be read."
            )

        reasons.append(
            "This component is capped at 8% of the overall score because the "
            "grey market is unofficial and unregulated."
        )
        return self._pack("gmp_signal", "Grey market signal", parts, expected,
                          inputs, reasons)

    def _risk(self, risk_factors, ipo, financials) -> ComponentScore:
        """Higher score = MORE risk. Inverted at blend time."""
        inputs: Dict[str, Any] = {}
        reasons: List[str] = []
        parts: List[float] = []
        expected = 4

        severity_points = {"HIGH": 85.0, "MEDIUM": 55.0, "LOW": 25.0}
        if risk_factors:
            scores = [
                severity_points.get(str(r.get("severity", "MEDIUM")).upper(), 55.0)
                for r in risk_factors
            ]
            parts.append(max(scores))
            inputs["risk_factor_count"] = len(risk_factors)
            inputs["high_severity_count"] = sum(
                1 for r in risk_factors
                if str(r.get("severity", "")).upper() == "HIGH"
            )
            reasons.append(
                f"{len(risk_factors)} risk factor(s) recorded, "
                f"{inputs['high_severity_count']} of them high severity."
            )
        else:
            reasons.append(
                "No risk factors were recorded. That is a gap in this system's "
                "data, not evidence that the offer document lists none."
            )

        ofs = ipo.get("ofs_cr")
        total = ipo.get("issue_size_cr")
        if ofs is not None and total:
            ofs_share = ofs / total * 100.0
            parts.append(_band(ofs_share, [(20, 25), (50, 45), (75, 70),
                                           (9999, 88)]))
            inputs["ofs_share_pct"] = round(ofs_share, 1)
            reasons.append(
                f"{ofs_share:.0f}% of the issue is an offer for sale, so that "
                f"portion goes to selling shareholders rather than the company."
            )

        if financials:
            latest = financials[-1]
            pat = latest.get("pat")
            if pat is not None and pat <= 0:
                parts.append(90.0)
                inputs["loss_making"] = True
                reasons.append(
                    "The company reported a loss in the latest disclosed period."
                )
            debt, equity = latest.get("total_debt"), latest.get("net_worth")
            if debt is not None and equity:
                ratio = debt / equity
                parts.append(_band(ratio, [(0.3, 20), (1.0, 45), (2.0, 70),
                                           (9999, 90)]))
                inputs["debt_to_equity"] = round(ratio, 2)
                reasons.append(f"Leverage stands at {ratio:.2f}x equity.")

        # Risk is blended worst-case, not averaged: a single severe disclosure
        # must not be diluted by three benign ones. Same rule the risk engine
        # applies - see RiskService._blend.
        return self._pack("risk", "Risk", parts, expected, inputs, reasons,
                          worst_case=True)

    # ------------------------------------------------------------------

    @staticmethod
    def _pack(key, label, parts, expected, inputs, reasons,
              worst_case: bool = False) -> ComponentScore:
        coverage = round(min(1.0, len(parts) / expected) * 100.0, 1)
        if not parts:
            return ComponentScore(key, label, None, coverage, inputs, reasons)
        mean = sum(parts) / len(parts)
        score = (
            round(0.6 * mean + 0.4 * max(parts), 1) if worst_case
            else round(mean, 1)
        )
        if worst_case and max(parts) >= 85:
            reasons.append(
                f"The most severe factor scores {max(parts):.0f}/100; the "
                f"component is blended 60% average / 40% worst so it cannot be "
                f"averaged away."
            )
        return ComponentScore(key, label, score, coverage, inputs, reasons)

    @staticmethod
    def _blend(components: List[ComponentScore]):
        weighted = 0.0
        weight_used = 0.0
        coverage_weighted = 0.0
        for component in components:
            weight = COMPONENT_WEIGHTS.get(component.key, 0.0)
            coverage_weighted += weight * (component.coverage_pct / 100.0)
            if component.score is None:
                continue
            value = (
                100.0 - component.score if component.key == "risk"
                else component.score
            )
            weighted += value * weight
            weight_used += weight
        overall = round(weighted / weight_used, 1) if weight_used else None
        completeness = round(coverage_weighted * 100.0, 1)
        return overall, completeness

    @staticmethod
    def _label(overall, components, completeness):
        if overall is None or completeness < 25:
            return ("Insufficient data",
                    f"Only {completeness:.0f}% of the expected inputs were "
                    f"available, which is not enough to characterise the issue.")

        by_key = {c.key: c for c in components}
        risk = by_key.get("risk")
        valuation = by_key.get("valuation_attractiveness")
        financial = by_key.get("financial_quality")

        # A count of severe disclosures is a fact about the offer document, so
        # it decides the label directly. Relying only on the blended score
        # would let arithmetic soften something the document states plainly.
        severe = (risk.inputs.get("high_severity_count") or 0) if risk else 0
        if severe >= 2:
            return ("High risk",
                    f"The offer document carries {severe} high-severity risk "
                    f"factors. That is decided by the disclosures themselves, "
                    f"not by the blended score.")
        if risk and risk.score is not None and risk.score >= 75:
            return ("High risk",
                    f"Risk scores {risk.score}/100, which dominates the profile "
                    f"regardless of the other components.")
        if financial and financial.score is not None and financial.score < 30:
            return ("Speculative",
                    f"Financial quality scores {financial.score}/100 - the "
                    f"reported profile does not support a fundamental case.")
        if overall >= 68:
            if valuation and valuation.score is not None and valuation.score < 40:
                return ("Positive but valuation sensitive",
                        f"Overall {overall}/100, but valuation scores only "
                        f"{valuation.score}/100 - the case depends on the price "
                        f"being right.")
            return ("Strong research profile",
                    f"Overall {overall}/100 with no component falling below the "
                    f"level that would qualify it.")
        if overall >= 50:
            return ("Neutral",
                    f"Overall {overall}/100 - the components neither reinforce "
                    f"nor contradict each other strongly.")
        return ("Weak research profile",
                f"Overall {overall}/100 across the components that could be "
                f"scored.")

    # ------------------------------------------------------------------

    @staticmethod
    def _valuation_detail(ipo, financials, peers) -> Dict[str, Any]:
        out: Dict[str, Any] = {"method": "post-issue implied multiples at the "
                                         "upper price band"}
        band_high = ipo.get("price_band_high")
        latest = financials[-1] if financials else None

        if latest and band_high:
            eps = latest.get("eps")
            if eps and eps > 0:
                out["implied_pe"] = round(band_high / eps, 2)
                out["eps_used"] = eps
                out["eps_period"] = latest.get("period_label")
            elif eps is not None and eps <= 0:
                out["implied_pe"] = None
                out["pe_note"] = (
                    "EPS is zero or negative, so a P/E multiple is undefined "
                    "rather than large."
                )

        if peers:
            pes = [p["pe"] for p in peers if p.get("pe") and p["pe"] > 0]
            ev_ebitdas = [p["ev_ebitda"] for p in peers
                          if p.get("ev_ebitda") and p["ev_ebitda"] > 0]
            roes = [p["roe"] for p in peers if p.get("roe") is not None]
            if pes:
                pes_sorted = sorted(pes)
                median = pes_sorted[len(pes_sorted) // 2]
                out["peer_median_pe"] = round(median, 2)
                out["peer_pe_range"] = [round(min(pes), 2), round(max(pes), 2)]
                if out.get("implied_pe"):
                    out["premium_to_peer_pct"] = round(
                        (out["implied_pe"] / median - 1.0) * 100.0, 1
                    )
                    out["verdict"] = (
                        "Valuation premium" if out["premium_to_peer_pct"] > 0
                        else "Valuation discount"
                    )
            if ev_ebitdas:
                s = sorted(ev_ebitdas)
                out["peer_median_ev_ebitda"] = round(s[len(s) // 2], 2)
            if roes:
                s = sorted(roes)
                out["peer_median_roe"] = round(s[len(s) // 2], 2)
            out["peer_count"] = len(peers)
            out["peers"] = peers
        else:
            out["peer_note"] = (
                "No peer set was configured for this issue, so the multiple "
                "cannot be placed in context."
            )
        return out

    @staticmethod
    def _swot(ipo, financials, risk_factors, valuation,
              subscription) -> Dict[str, List[Dict[str, str]]]:
        """Every point carries the evidence that produced it."""
        strengths, weaknesses, opportunities, threats = [], [], [], []

        if financials:
            latest = financials[-1]
            revenues = [f.get("revenue") for f in financials if f.get("revenue")]
            if len(revenues) >= 3 and revenues[-1] > revenues[0]:
                years = len(revenues) - 1
                cagr = ((revenues[-1] / revenues[0]) ** (1 / years) - 1) * 100
                strengths.append({
                    "point": f"Revenue compounded at {cagr:.1f}% over {years} year(s).",
                    "evidence": f"Revenue moved from {revenues[0]:,.0f} to "
                                f"{revenues[-1]:,.0f} in the disclosed periods.",
                })
            if (latest.get("roe") or 0) >= 15:
                strengths.append({
                    "point": f"Return on equity of {latest['roe']:.1f}%.",
                    "evidence": f"Latest disclosed period: {latest.get('period_label')}.",
                })
            if (latest.get("pat") or 0) <= 0:
                weaknesses.append({
                    "point": "The company is loss-making in the latest period.",
                    "evidence": f"PAT of {latest.get('pat')} for "
                                f"{latest.get('period_label')}.",
                })
            debt, equity = latest.get("total_debt"), latest.get("net_worth")
            if debt is not None and equity and debt / equity > 1.5:
                weaknesses.append({
                    "point": f"Leverage of {debt / equity:.2f}x equity.",
                    "evidence": f"Total debt {debt:,.0f} against net worth "
                                f"{equity:,.0f}.",
                })
        else:
            weaknesses.append({
                "point": "No financial history is available in this system.",
                "evidence": "The ipo_financials table holds no rows for this issue.",
            })

        premium = valuation.get("premium_to_peer_pct")
        if premium is not None:
            target = weaknesses if premium > 15 else strengths
            target.append({
                "point": f"Priced at a {abs(premium):.0f}% "
                         f"{'premium' if premium > 0 else 'discount'} to peers.",
                "evidence": f"Implied P/E {valuation.get('implied_pe')} against a "
                            f"peer median of {valuation.get('peer_median_pe')} "
                            f"across {valuation.get('peer_count')} companies.",
            })

        if ipo.get("fresh_issue_cr") and ipo.get("issue_size_cr"):
            fresh_share = ipo["fresh_issue_cr"] / ipo["issue_size_cr"] * 100
            if fresh_share >= 60:
                opportunities.append({
                    "point": f"{fresh_share:.0f}% of proceeds are fresh capital "
                             f"going into the business.",
                    "evidence": f"Fresh issue {ipo['fresh_issue_cr']} crore of a "
                                f"{ipo['issue_size_cr']} crore issue.",
                })
        if ipo.get("use_of_proceeds"):
            opportunities.append({
                "point": "Stated use of proceeds is disclosed in the offer document.",
                "evidence": str(ipo["use_of_proceeds"])[:300],
            })

        for factor in risk_factors[:6]:
            threats.append({
                "point": f"{str(factor.get('category', '')).replace('_', ' ').title()}: "
                         f"{str(factor.get('description', ''))[:180]}",
                "evidence": (
                    f"Severity {factor.get('severity')}"
                    + (f", quantum {factor.get('quantum')}{factor.get('quantum_unit') or ''}"
                       if factor.get("quantum") else "")
                    + ". Source: offer document as recorded."
                ),
            })

        if subscription and (subscription.get("total_times") or 0) < 1:
            threats.append({
                "point": f"Under-subscribed at {subscription['total_times']:.2f}x.",
                "evidence": "Latest recorded subscription snapshot.",
            })

        if not threats:
            threats.append({
                "point": "No threats recorded - this is a data gap, not a clean bill.",
                "evidence": "No rows in ipo_risk_factors for this issue.",
            })

        return {"strengths": strengths, "weaknesses": weaknesses,
                "opportunities": opportunities, "threats": threats}

    # ------------------------------------------------------------------

    @staticmethod
    def simulate_application(
        capital: float, lots: int, lot_size: int, price: float,
        gmp: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Application economics under four explicit GMP scenarios."""
        shares = lots * lot_size
        application_amount = shares * price
        affordable = application_amount <= capital

        def _scenario(name: str, listing_price: float,
                      description: str) -> Dict[str, Any]:
            gain = (listing_price - price) * shares
            return {
                "scenario": name,
                "description": description,
                "assumed_listing_price": round(listing_price, 2),
                "gross_gain": round(gain, 2),
                "gain_pct": round((listing_price / price - 1.0) * 100.0, 2),
                "value_at_listing": round(listing_price * shares, 2),
            }

        current_gmp = gmp or 0.0
        scenarios = [
            _scenario("GMP holds", price + current_gmp,
                      "The quoted premium is realised exactly at listing. "
                      "Historically this is the exception, not the rule."),
            _scenario("GMP halves", price + current_gmp * 0.5,
                      "The premium erodes by half between now and listing."),
            _scenario("GMP rises 50%", price + current_gmp * 1.5,
                      "The premium expands further before listing."),
            _scenario("Lists below issue price", price * 0.9,
                      "A 10% discount listing. This happens regularly, "
                      "including for issues that were heavily subscribed."),
        ]

        return {
            "inputs": {
                "capital": capital, "lots": lots, "lot_size": lot_size,
                "price_per_share": price, "gmp": gmp,
            },
            "shares_applied_for": shares,
            "application_amount": round(application_amount, 2),
            "affordable": affordable,
            "scenarios": scenarios,
            "allotment_note": (
                "Every scenario assumes full allotment. In an oversubscribed "
                "retail book allotment is by lottery, so the most likely "
                "outcome for a single-lot application is no allotment at all."
            ),
            "disclaimer": (
                "Grey Market Premium is an unofficial indicator quoted by "
                "private dealers. It is not a listing price and carries no "
                "guarantee whatsoever."
            ),
        }


def _band(value: float, bands: List[tuple[float, float]]) -> float:
    for bound, score in bands:
        if value <= bound:
            return float(score)
    return float(bands[-1][1])


ipo_analysis_service = IpoAnalysisService()
