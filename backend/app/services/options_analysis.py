"""Options intelligence: PCR, max pain, OI build-up, IV structure, key strikes.

Every conclusion is phrased as an observation about positioning, never as a
prediction. "Call OI is concentrated at 1440" is a fact; "the stock will stop
at 1440" is not, and this module will not say it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from app.core.data_quality import Sourced
from app.providers.base import OptionChainData, OptionLeg
from app.services import greeks as gk
from app.services.evidence import EvidenceChain, EvidenceItem, Stance

METHODOLOGY = "/methodology#options"


@dataclass
class StrikeRow:
    strike: float
    moneyness: str
    distance_pct: Optional[float]
    call: Dict[str, Any] = field(default_factory=dict)
    put: Dict[str, Any] = field(default_factory=dict)


@dataclass
class OptionsView:
    underlying_symbol: str
    expiry: date
    captured_at: datetime
    underlying_value: Optional[float]
    atm_strike: Optional[float]
    rows: List[StrikeRow]
    totals: Dict[str, Any]
    key_levels: Dict[str, Any]
    iv_structure: Dict[str, Any]
    chain: EvidenceChain
    available_expiries: List[date]
    greeks_assumptions: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "underlying_symbol": self.underlying_symbol,
            "expiry": self.expiry.isoformat(),
            "captured_at": self.captured_at.isoformat(),
            "underlying_value": self.underlying_value,
            "atm_strike": self.atm_strike,
            "available_expiries": [e.isoformat() for e in self.available_expiries],
            "rows": [
                {
                    "strike": r.strike, "moneyness": r.moneyness,
                    "distance_pct": r.distance_pct, "call": r.call, "put": r.put,
                }
                for r in self.rows
            ],
            "totals": self.totals,
            "key_levels": self.key_levels,
            "iv_structure": self.iv_structure,
            "greeks_assumptions": self.greeks_assumptions,
            "score": self.chain.score,
            "stance": self.chain.stance.value,
            "explanation": self.chain.explain(),
            "evidence_chain": self.chain.to_dict(),
        }


class OptionsAnalysisService:

    def analyse(
        self,
        envelope: Sourced[OptionChainData],
        risk_free_rate: float = gk.DEFAULT_RISK_FREE_RATE,
        dividend_yield: float = gk.DEFAULT_DIVIDEND_YIELD,
        compute_greeks: bool = True,
        strikes_around_atm: int = 15,
    ) -> Optional[OptionsView]:
        data = envelope.value
        if data is None or not data.legs:
            return None

        source, status = envelope.source_name, envelope.status.value
        observed = envelope.observed_at or data.captured_at
        spot = data.underlying_value

        by_strike: Dict[float, Dict[str, OptionLeg]] = {}
        for leg in data.legs:
            by_strike.setdefault(leg.strike, {})[leg.option_type] = leg

        strikes = sorted(by_strike)
        atm = self._atm_strike(strikes, spot)

        rows = self._build_rows(
            by_strike, strikes, atm, spot, data.expiry, risk_free_rate,
            dividend_yield, compute_greeks, strikes_around_atm, observed,
        )
        totals = self._totals(by_strike, strikes)
        max_pain, pain_curve = self._max_pain(by_strike, strikes)
        totals["max_pain"] = max_pain

        key_levels = self._key_levels(by_strike, strikes, spot, max_pain,
                                      pain_curve)
        iv_structure = self._iv_structure(rows, atm)

        chain = EvidenceChain(dimension="OPTIONS", methodology_ref=METHODOLOGY)
        self._pcr_evidence(chain, totals, source, status, observed)
        self._concentration_evidence(chain, key_levels, spot, source, status,
                                     observed)
        self._buildup_evidence(chain, rows, source, status, observed)
        self._iv_evidence(chain, iv_structure, source, status, observed)
        self._expiry_evidence(chain, data.expiry, source, status, observed)
        chain.finalise()
        chain.summary = chain.explain()

        chain.limit(
            "Open interest shows where positions sit, not who holds them or "
            "why. A large call OI can equally be a covered writer or an "
            "outright bet, and the chain cannot tell them apart."
        )
        chain.limit(
            "Max pain is an arithmetic minimum of writer payout at expiry "
            "given today's OI. Open interest changes daily, so the level moves."
        )
        if envelope.is_demo:
            chain.limit("Computed from seeded demonstration chain data.")

        assumptions = gk.GreekAssumptions(
            risk_free_rate=risk_free_rate, dividend_yield=dividend_yield
        ).to_dict()

        return OptionsView(
            underlying_symbol=data.underlying_symbol,
            expiry=data.expiry,
            captured_at=observed,
            underlying_value=spot,
            atm_strike=atm,
            rows=rows,
            totals=totals,
            key_levels=key_levels,
            iv_structure=iv_structure,
            chain=chain,
            available_expiries=data.available_expiries,
            greeks_assumptions=assumptions,
        )

    # ------------------------------------------------------------------

    @staticmethod
    def _atm_strike(strikes: List[float], spot: Optional[float]) -> Optional[float]:
        if not strikes or spot is None:
            return None
        return min(strikes, key=lambda k: abs(k - spot))

    def _build_rows(
        self, by_strike, strikes, atm, spot, expiry, r, q, do_greeks,
        window, observed,
    ) -> List[StrikeRow]:
        if atm is not None and window:
            atm_index = strikes.index(atm)
            lo = max(0, atm_index - window)
            hi = min(len(strikes), atm_index + window + 1)
            selected = strikes[lo:hi]
        else:
            selected = strikes

        rows: List[StrikeRow] = []
        for strike in selected:
            legs = by_strike[strike]
            row = StrikeRow(
                strike=strike,
                moneyness=(
                    "ATM" if strike == atm else
                    ("ITM" if spot and strike < spot else "OTM")
                ),
                distance_pct=round((strike / spot - 1.0) * 100.0, 2)
                if spot else None,
            )
            for side, bucket in (("CE", "call"), ("PE", "put")):
                leg = legs.get(side)
                if leg is None:
                    continue
                payload: Dict[str, Any] = {
                    "ltp": leg.ltp,
                    "change": leg.change,
                    "change_pct": leg.change_pct,
                    "open_interest": leg.open_interest,
                    "oi_change": leg.oi_change,
                    "oi_change_pct": (
                        round(leg.oi_change / (leg.open_interest - leg.oi_change)
                              * 100.0, 2)
                        if leg.oi_change and leg.open_interest
                        and (leg.open_interest - leg.oi_change) > 0 else None
                    ),
                    "volume": leg.volume,
                    "implied_volatility": leg.implied_volatility,
                    "bid": leg.bid, "ask": leg.ask,
                    "bid_qty": leg.bid_qty, "ask_qty": leg.ask_qty,
                    "spread_pct": (
                        round((leg.ask - leg.bid) / leg.ask * 100.0, 2)
                        if leg.bid and leg.ask and leg.ask > 0 else None
                    ),
                    "buildup": classify_buildup(leg.change, leg.oi_change),
                    "moneyness": gk.classify_moneyness(spot, strike, side)
                    if spot else "UNKNOWN",
                }
                if do_greeks and spot:
                    greek = gk.compute_greeks(
                        spot=spot, strike=strike, expiry=expiry,
                        option_type=side, market_price=leg.ltp,
                        volatility=(leg.implied_volatility / 100.0
                                    if leg.implied_volatility else None),
                        risk_free_rate=r, dividend_yield=q, now=observed,
                    )
                    payload["greeks"] = {
                        "delta": greek.delta, "gamma": greek.gamma,
                        "theta": greek.theta, "vega": greek.vega,
                        "rho": greek.rho,
                        "implied_volatility": (
                            round(greek.implied_volatility * 100.0, 2)
                            if greek.implied_volatility is not None else None
                        ),
                        "theoretical_price": greek.price,
                        "intrinsic_value": greek.intrinsic_value,
                        "time_value": greek.time_value,
                        "converged": greek.converged,
                        "failure_reason": greek.failure_reason,
                        "explanations": greek.explanations,
                    }
                setattr(row, bucket, payload)
            rows.append(row)
        return rows

    @staticmethod
    def _totals(by_strike, strikes) -> Dict[str, Any]:
        call_oi = sum((by_strike[k].get("CE").open_interest or 0)
                      for k in strikes if by_strike[k].get("CE"))
        put_oi = sum((by_strike[k].get("PE").open_interest or 0)
                     for k in strikes if by_strike[k].get("PE"))
        call_vol = sum((by_strike[k].get("CE").volume or 0)
                       for k in strikes if by_strike[k].get("CE"))
        put_vol = sum((by_strike[k].get("PE").volume or 0)
                      for k in strikes if by_strike[k].get("PE"))
        call_oi_chg = sum((by_strike[k].get("CE").oi_change or 0)
                          for k in strikes if by_strike[k].get("CE"))
        put_oi_chg = sum((by_strike[k].get("PE").oi_change or 0)
                         for k in strikes if by_strike[k].get("PE"))
        return {
            "total_call_oi": call_oi,
            "total_put_oi": put_oi,
            "total_call_volume": call_vol,
            "total_put_volume": put_vol,
            "call_oi_change": call_oi_chg,
            "put_oi_change": put_oi_chg,
            "pcr_oi": round(put_oi / call_oi, 3) if call_oi else None,
            "pcr_volume": round(put_vol / call_vol, 3) if call_vol else None,
            "strike_count": len(strikes),
            "pcr_formula": "PCR(OI) = total put open interest / total call open interest",
        }

    @staticmethod
    def _max_pain(by_strike, strikes) -> Tuple[Optional[float], List[Dict[str, Any]]]:
        """Strike at which total writer payout is smallest.

        For each candidate expiry price K, option writers pay:
            sum over call strikes s < K of CE_OI(s) * (K - s)
          + sum over put strikes  s > K of PE_OI(s) * (s - K)
        """
        if not strikes:
            return None, []
        curve: List[Dict[str, Any]] = []
        for candidate in strikes:
            call_pain = sum(
                (by_strike[s].get("CE").open_interest or 0) * (candidate - s)
                for s in strikes
                if s < candidate and by_strike[s].get("CE")
            )
            put_pain = sum(
                (by_strike[s].get("PE").open_interest or 0) * (s - candidate)
                for s in strikes
                if s > candidate and by_strike[s].get("PE")
            )
            curve.append({
                "strike": candidate,
                "total_pain": call_pain + put_pain,
                "call_pain": call_pain,
                "put_pain": put_pain,
            })
        if not any(point["total_pain"] for point in curve):
            return None, curve
        best = min(curve, key=lambda point: point["total_pain"])
        return best["strike"], curve

    @staticmethod
    def _key_levels(by_strike, strikes, spot, max_pain, pain_curve) -> Dict[str, Any]:
        def _top(side: str, key: str, n: int = 3) -> List[Dict[str, Any]]:
            rows = [
                {"strike": k, "value": getattr(by_strike[k][side], key) or 0}
                for k in strikes if by_strike[k].get(side)
            ]
            rows.sort(key=lambda r: r["value"], reverse=True)
            return rows[:n]

        highest_call_oi = _top("CE", "open_interest")
        highest_put_oi = _top("PE", "open_interest")
        call_additions = _top("CE", "oi_change")
        put_additions = _top("PE", "oi_change")

        return {
            "highest_call_oi": highest_call_oi,
            "highest_put_oi": highest_put_oi,
            "largest_call_oi_additions": call_additions,
            "largest_put_oi_additions": put_additions,
            "max_pain": max_pain,
            "max_pain_distance_pct": (
                round((max_pain / spot - 1.0) * 100.0, 2)
                if max_pain and spot else None
            ),
            "pain_curve": pain_curve[:60],
            "observation": (
                "Highest call open interest marks where the most calls are "
                "written; highest put open interest marks the same for puts. "
                "These are positioning facts, not barriers."
            ),
        }

    @staticmethod
    def _iv_structure(rows: List[StrikeRow], atm: Optional[float]) -> Dict[str, Any]:
        call_ivs = [(r.strike, r.call.get("implied_volatility"))
                    for r in rows if r.call.get("implied_volatility")]
        put_ivs = [(r.strike, r.put.get("implied_volatility"))
                   for r in rows if r.put.get("implied_volatility")]
        if not call_ivs and not put_ivs:
            return {"available": False,
                    "note": "The provider supplied no implied volatility."}

        atm_iv = None
        if atm is not None:
            for strike, iv in call_ivs + put_ivs:
                if strike == atm:
                    atm_iv = iv
                    break

        all_ivs = [iv for _, iv in call_ivs + put_ivs]
        otm_put_ivs = [iv for strike, iv in put_ivs if atm and strike < atm]
        otm_call_ivs = [iv for strike, iv in call_ivs if atm and strike > atm]

        skew = None
        if otm_put_ivs and otm_call_ivs:
            skew = round(float(np.mean(otm_put_ivs) - np.mean(otm_call_ivs)), 2)

        return {
            "available": True,
            "atm_iv": atm_iv,
            "mean_iv": round(float(np.mean(all_ivs)), 2) if all_ivs else None,
            "min_iv": round(float(np.min(all_ivs)), 2) if all_ivs else None,
            "max_iv": round(float(np.max(all_ivs)), 2) if all_ivs else None,
            "put_call_skew": skew,
            "skew_reading": (
                None if skew is None else
                "Out-of-the-money puts carry higher implied volatility than "
                "equivalent calls - the usual shape in equity markets, and it "
                "steepens when downside protection is being bid."
                if skew > 1 else
                "Out-of-the-money calls carry higher implied volatility than "
                "puts - an unusual shape that shows up around upside events."
                if skew < -1 else
                "Implied volatility is broadly symmetric across the wings."
            ),
            "curve": [
                {"strike": s, "call_iv": next((iv for k, iv in call_ivs if k == s), None),
                 "put_iv": next((iv for k, iv in put_ivs if k == s), None)}
                for s in sorted({s for s, _ in call_ivs + put_ivs})
            ],
        }

    # ------------------------------------------------------------------
    # evidence
    # ------------------------------------------------------------------

    def _pcr_evidence(self, chain, totals, source, status, observed) -> None:
        pcr = totals.get("pcr_oi")
        if pcr is None:
            chain.note_gap("PCR unavailable - open interest missing")
            return
        if pcr >= 1.3:
            stance, reading = Stance.POSITIVE, (
                "more puts than calls are open, which is conventionally read as "
                "put writers being comfortable - though it also marks crowded "
                "positioning that unwinds sharply"
            )
        elif pcr <= 0.7:
            stance, reading = Stance.NEGATIVE, (
                "more calls than puts are open, conventionally read as call "
                "writers being comfortable"
            )
        else:
            stance, reading = Stance.NEUTRAL, "positioning is balanced"
        chain.add(EvidenceItem(
            metric="Put/Call ratio (OI)", value=pcr, stance=stance, weight=1.5,
            calculation=(
                f"{totals['total_put_oi']:,} put OI / "
                f"{totals['total_call_oi']:,} call OI"
            ),
            interpretation=f"PCR is {pcr} - {reading}",
            source=source, data_status=status, observed_at=observed,
        ))

    def _concentration_evidence(self, chain, key_levels, spot, source, status,
                                observed) -> None:
        top_call = (key_levels.get("highest_call_oi") or [{}])[0]
        top_put = (key_levels.get("highest_put_oi") or [{}])[0]
        if top_call.get("strike") and top_put.get("strike"):
            chain.add(EvidenceItem(
                metric="OI concentration band",
                value=f"{top_put['strike']:g} - {top_call['strike']:g}",
                stance=Stance.NEUTRAL, weight=1.2,
                calculation=(
                    f"highest put OI {top_put['value']:,} at {top_put['strike']:g}; "
                    f"highest call OI {top_call['value']:,} at {top_call['strike']:g}"
                ),
                interpretation=(
                    f"Call open interest is heaviest at {top_call['strike']:g} and "
                    f"put open interest at {top_put['strike']:g}. This is a "
                    f"positioning band worth watching, not a price ceiling or floor."
                ),
                source=source, data_status=status, observed_at=observed,
            ))
        max_pain = key_levels.get("max_pain")
        if max_pain and spot:
            distance = key_levels.get("max_pain_distance_pct")
            chain.add(EvidenceItem(
                metric="Max pain", value=max_pain, stance=Stance.NEUTRAL,
                weight=0.8,
                calculation="strike minimising total writer payout at expiry",
                interpretation=(
                    f"Max pain sits at {max_pain:g}, {distance:+.2f}% from spot. "
                    f"It is an arithmetic property of today's open interest and "
                    f"shifts as that open interest changes."
                ),
                source=source, data_status=status, observed_at=observed,
            ))

    def _buildup_evidence(self, chain, rows, source, status, observed) -> None:
        counts: Dict[str, int] = {}
        for row in rows:
            for side in ("call", "put"):
                buildup = (getattr(row, side) or {}).get("buildup")
                if buildup and buildup != "UNCLEAR":
                    counts[f"{side}:{buildup}"] = counts.get(f"{side}:{buildup}", 0) + 1
        if not counts:
            chain.note_gap("OI change data unavailable - build-up not classified")
            return
        dominant = max(counts.items(), key=lambda kv: kv[1])
        side, pattern = dominant[0].split(":")
        bullish = (
            (side == "put" and pattern in ("SHORT_BUILDUP", "LONG_UNWINDING"))
            or (side == "call" and pattern in ("LONG_BUILDUP", "SHORT_COVERING"))
        )
        chain.add(EvidenceItem(
            metric="Dominant OI build-up",
            value=f"{side} {pattern.replace('_', ' ').lower()}",
            stance=Stance.POSITIVE if bullish else Stance.NEGATIVE, weight=1.3,
            calculation=f"{dominant[1]} of {len(rows)} strikes show this pattern",
            interpretation=(
                f"The most common pattern across the visible strikes is "
                f"{pattern.replace('_', ' ').lower()} on the {side} side, "
                f"classified from the sign of price change against the sign of "
                f"open-interest change."
            ),
            source=source, data_status=status, observed_at=observed,
        ))

    def _iv_evidence(self, chain, iv_structure, source, status, observed) -> None:
        if not iv_structure.get("available"):
            chain.note_gap("implied volatility not supplied by the provider")
            return
        atm_iv = iv_structure.get("atm_iv")
        if atm_iv:
            chain.add(EvidenceItem(
                metric="ATM implied volatility", value=atm_iv,
                stance=Stance.NEUTRAL, weight=1.0, unit="%",
                calculation="implied volatility at the strike nearest spot",
                interpretation=(
                    f"The market is pricing {atm_iv:.1f}% annualised volatility "
                    f"at the money. Buying premium here is a bet that realised "
                    f"volatility exceeds it; selling is the opposite bet."
                ),
                source=source, data_status=status, observed_at=observed,
            ))
        skew = iv_structure.get("put_call_skew")
        if skew is not None:
            chain.add(EvidenceItem(
                metric="Put-call IV skew", value=skew,
                stance=Stance.NEGATIVE if skew > 3 else Stance.NEUTRAL,
                weight=0.9, unit="vol points",
                calculation="mean OTM put IV - mean OTM call IV",
                interpretation=iv_structure.get("skew_reading") or "",
                source=source, data_status=status, observed_at=observed,
            ))

    def _expiry_evidence(self, chain, expiry, source, status, observed) -> None:
        days = (expiry - (observed.date() if observed else date.today())).days
        if days <= 2:
            stance, reading = Stance.NEGATIVE, (
                "gamma and theta both dominate in the last two sessions; small "
                "spot moves produce outsized premium swings"
            )
        elif days <= 7:
            stance, reading = Stance.NEGATIVE, (
                "time decay accelerates inside the final week"
            )
        else:
            stance, reading = Stance.NEUTRAL, "decay is not yet the dominant force"
        chain.add(EvidenceItem(
            metric="Days to expiry", value=days, stance=stance, weight=1.1,
            unit="days", calculation=f"expiry {expiry.isoformat()} minus today",
            interpretation=f"{days} days to expiry - {reading}",
            source=source, data_status=status, observed_at=observed,
        ))


def classify_buildup(price_change: Optional[float],
                     oi_change: Optional[int]) -> str:
    """The standard four-quadrant reading of price change vs OI change.

    price up   + OI up   -> longs are being added        (LONG_BUILDUP)
    price down + OI up   -> shorts are being added       (SHORT_BUILDUP)
    price up   + OI down -> shorts are closing           (SHORT_COVERING)
    price down + OI down -> longs are closing            (LONG_UNWINDING)

    This describes flow, not intent: it cannot distinguish a directional bet
    from a hedge.
    """
    if price_change is None or oi_change is None:
        return "UNCLEAR"
    if abs(price_change) < 1e-9 or oi_change == 0:
        return "UNCLEAR"
    if price_change > 0 and oi_change > 0:
        return "LONG_BUILDUP"
    if price_change < 0 and oi_change > 0:
        return "SHORT_BUILDUP"
    if price_change > 0 and oi_change < 0:
        return "SHORT_COVERING"
    return "LONG_UNWINDING"


options_analysis_service = OptionsAnalysisService()
