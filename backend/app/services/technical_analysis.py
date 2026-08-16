"""Technical analysis with a full evidence trail.

Produces an `EvidenceChain` whose every item names the metric, its value, the
comparison that was made and how much it weighed. Nothing here ever emits a
conclusion the items do not support - `EvidenceChain.explain()` is built from
the items themselves.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from app.core.data_quality import Sourced
from app.providers.base import Bar
from app.services import indicators as ind
from app.services.evidence import EvidenceChain, EvidenceItem, Stance

METHODOLOGY = "/methodology#technical"


def bars_to_frame(bars: List[Bar]) -> pd.DataFrame:
    """List[Bar] -> tidy OHLCV DataFrame, de-duplicated and time-sorted."""
    if not bars:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    frame = pd.DataFrame(
        [
            {
                "time": b.time, "open": b.open, "high": b.high,
                "low": b.low, "close": b.close, "volume": b.volume,
            }
            for b in bars
        ]
    )
    frame = (
        frame.dropna(subset=["open", "high", "low", "close"])
        .drop_duplicates(subset=["time"], keep="last")
        .sort_values("time")
        .set_index("time")
    )
    frame.index = pd.DatetimeIndex(frame.index)
    return frame


@dataclass
class TechnicalView:
    symbol: str
    interval: str
    as_of: Optional[datetime]
    last_close: Optional[float]
    indicators: Dict[str, Optional[float]]
    levels: List[Dict[str, Any]]
    gap: Optional[Dict[str, Any]]
    divergence: Optional[Dict[str, Any]]
    regime: Dict[str, Any]
    chain: EvidenceChain
    bars_used: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "interval": self.interval,
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "last_close": self.last_close,
            "bars_used": self.bars_used,
            "indicators": self.indicators,
            "levels": self.levels,
            "gap": self.gap,
            "divergence": self.divergence,
            "regime": self.regime,
            "score": self.chain.score,
            "stance": self.chain.stance.value,
            "explanation": self.chain.explain(),
            "evidence_chain": self.chain.to_dict(),
        }


class TechnicalAnalysisService:
    """Stateless. Give it bars, get back a scored, explained view."""

    MIN_BARS = 30

    def analyse(
        self,
        symbol: str,
        bars_envelope: Sourced[List[Bar]],
        interval: str = "1d",
    ) -> TechnicalView:
        chain = EvidenceChain(dimension="TECHNICAL",
                              methodology_ref=METHODOLOGY)
        bars = bars_envelope.value or []
        frame = bars_to_frame(bars)

        source = bars_envelope.source_name
        status = bars_envelope.status.value
        observed = bars_envelope.observed_at

        if len(frame) < self.MIN_BARS:
            chain.note_gap(
                f"only {len(frame)} bars available; at least {self.MIN_BARS} "
                "are needed before indicators are meaningful"
            )
            chain.limit(
                "No technical conclusion is offered on this few bars. This is a "
                "data limitation, not a neutral reading."
            )
            return TechnicalView(
                symbol=symbol, interval=interval, as_of=observed,
                last_close=float(frame["close"].iloc[-1]) if len(frame) else None,
                indicators={}, levels=[], gap=None, divergence=None,
                regime={"regime": "INSUFFICIENT_DATA", "reasons": []},
                chain=chain.finalise(), bars_used=len(frame),
            )

        enriched = ind.compute_all(frame)
        last = enriched.iloc[-1]
        close = float(last["close"])

        snapshot = self._snapshot(enriched)

        # ---- trend: price versus its own moving averages -----------------
        self._ma_evidence(chain, close, snapshot, source, status, observed)
        # ---- trend strength ---------------------------------------------
        self._adx_evidence(chain, snapshot, source, status, observed)
        # ---- momentum ----------------------------------------------------
        self._rsi_evidence(chain, snapshot, source, status, observed)
        self._macd_evidence(chain, snapshot, source, status, observed)
        # ---- volume ------------------------------------------------------
        self._volume_evidence(chain, snapshot, source, status, observed)
        # ---- volatility --------------------------------------------------
        self._volatility_evidence(chain, snapshot, source, status, observed)
        # ---- position in the 52-week range -------------------------------
        self._range_evidence(chain, snapshot, source, status, observed)
        # ---- supertrend --------------------------------------------------
        self._supertrend_evidence(chain, close, snapshot, source, status, observed)

        levels = ind.support_resistance_levels(
            enriched["high"], enriched["low"], enriched["close"],
            enriched.get("volume"),
        )
        self._level_evidence(chain, close, levels, source, status, observed)

        gap = ind.gap_analysis(enriched["open"], enriched["close"])
        divergence = ind.rsi_divergence(enriched["close"], enriched["rsi_14"])
        if divergence:
            chain.add(EvidenceItem(
                metric="RSI divergence",
                value=divergence["type"],
                stance=Stance.NEGATIVE if "BEARISH" in divergence["type"]
                else Stance.POSITIVE,
                weight=1.2,
                calculation=(
                    f"price {divergence['price_from']} -> {divergence['price_to']}, "
                    f"RSI {divergence['rsi_from']} -> {divergence['rsi_to']}"
                ),
                interpretation=divergence["note"],
                source=source, data_status=status, observed_at=observed,
            ))

        chain.finalise()
        chain.summary = chain.explain()

        if bars_envelope.is_demo:
            chain.limit(
                "Computed from seeded demonstration bars, not market data."
            )
        chain.limit(
            "Indicators describe what price has already done. They are not a "
            "forecast and carry no probability of any future outcome."
        )

        return TechnicalView(
            symbol=symbol,
            interval=interval,
            as_of=observed or enriched.index[-1].to_pydatetime(),
            last_close=round(close, 2),
            indicators=snapshot,
            levels=levels,
            gap=gap,
            divergence=divergence,
            regime=self._regime(snapshot, chain),
            chain=chain,
            bars_used=len(enriched),
        )

    # ------------------------------------------------------------------
    # evidence builders
    # ------------------------------------------------------------------

    @staticmethod
    def _snapshot(frame: pd.DataFrame) -> Dict[str, Optional[float]]:
        last = frame.iloc[-1]
        keys = [
            "close", "sma_20", "sma_50", "sma_100", "sma_200", "ema_9",
            "ema_20", "ema_50", "rsi_14", "macd", "macd_signal", "macd_hist",
            "atr_14", "atr_pct", "adx_14", "plus_di", "minus_di", "bb_upper",
            "bb_lower", "bb_width", "stoch_k", "stoch_d", "supertrend",
            "supertrend_dir", "vwap", "volume_ratio_20", "hist_vol_20",
            "pct_from_52w_high", "pct_from_52w_low",
        ]
        out: Dict[str, Optional[float]] = {}
        for key in keys:
            value = last.get(key)
            out[key] = None if value is None or pd.isna(value) else round(
                float(value), 4
            )
        # A previous-bar MACD histogram lets us detect a *fresh* crossover.
        if len(frame) >= 2:
            prev_hist = frame["macd_hist"].iloc[-2]
            out["macd_hist_prev"] = (
                None if pd.isna(prev_hist) else round(float(prev_hist), 4)
            )
        return out

    def _ma_evidence(self, chain, close, s, source, status, observed) -> None:
        above = []
        below = []
        for key, label in (("sma_20", "20-DMA"), ("sma_50", "50-DMA"),
                           ("sma_200", "200-DMA")):
            value = s.get(key)
            if value is None:
                chain.note_gap(f"{label} unavailable (insufficient history)")
                continue
            (above if close > value else below).append((label, value))

        for label, value in above:
            chain.add(EvidenceItem(
                metric=f"Price vs {label}", value=round(close - value, 2),
                stance=Stance.POSITIVE,
                weight=2.0 if "200" in label else 1.5,
                calculation=f"close {close:.2f} - {label} {value:.2f}",
                interpretation=f"price is {((close/value)-1)*100:.2f}% above its {label}",
                unit="INR", source=source, data_status=status, observed_at=observed,
            ))
        for label, value in below:
            chain.add(EvidenceItem(
                metric=f"Price vs {label}", value=round(close - value, 2),
                stance=Stance.NEGATIVE,
                weight=2.0 if "200" in label else 1.5,
                calculation=f"close {close:.2f} - {label} {value:.2f}",
                interpretation=f"price is {((close/value)-1)*100:.2f}% below its {label}",
                unit="INR", source=source, data_status=status, observed_at=observed,
            ))

        sma20, sma50 = s.get("sma_20"), s.get("sma_50")
        if sma20 is not None and sma50 is not None:
            aligned = sma20 > sma50
            chain.add(EvidenceItem(
                metric="Moving-average alignment",
                value="20 > 50" if aligned else "20 < 50",
                stance=Stance.POSITIVE if aligned else Stance.NEGATIVE,
                weight=1.5,
                calculation=f"SMA20 {sma20:.2f} vs SMA50 {sma50:.2f}",
                interpretation=(
                    "shorter average above the longer one - the recent trend is "
                    "the stronger of the two"
                    if aligned else
                    "shorter average below the longer one"
                ),
                source=source, data_status=status, observed_at=observed,
            ))

    def _adx_evidence(self, chain, s, source, status, observed) -> None:
        adx_value = s.get("adx_14")
        if adx_value is None:
            chain.note_gap("ADX unavailable")
            return
        plus_di, minus_di = s.get("plus_di"), s.get("minus_di")
        directional = (
            "up" if (plus_di or 0) > (minus_di or 0) else "down"
        )
        if adx_value >= 25:
            stance = Stance.POSITIVE if directional == "up" else Stance.NEGATIVE
            reading = f"a trending market ({directional}side)"
        elif adx_value < 20:
            stance = Stance.NEUTRAL
            reading = "a range-bound market with no dominant direction"
        else:
            stance = Stance.NEUTRAL
            reading = "a developing but not yet established trend"
        chain.add(EvidenceItem(
            metric="ADX(14)", value=round(adx_value, 1), stance=stance,
            weight=1.5,
            calculation=f"ADX {adx_value:.1f}, +DI {plus_di}, -DI {minus_di}",
            interpretation=f"ADX {adx_value:.1f} indicates {reading}",
            source=source, data_status=status, observed_at=observed,
        ))

    def _rsi_evidence(self, chain, s, source, status, observed) -> None:
        value = s.get("rsi_14")
        if value is None:
            chain.note_gap("RSI(14) unavailable")
            return
        if value >= 70:
            stance, reading = Stance.NEGATIVE, (
                "above 70 - stretched; historically this has preceded both "
                "continuation in strong trends and mean reversion"
            )
        elif value >= 55:
            stance, reading = Stance.POSITIVE, "above 55 - momentum favours buyers"
        elif value <= 30:
            stance, reading = Stance.POSITIVE, (
                "below 30 - washed out; a bounce condition, not a bottom signal"
            )
        elif value <= 45:
            stance, reading = Stance.NEGATIVE, "below 45 - momentum favours sellers"
        else:
            stance, reading = Stance.NEUTRAL, "between 45 and 55 - no momentum edge"
        chain.add(EvidenceItem(
            metric="RSI(14)", value=round(value, 1), stance=stance, weight=1.5,
            calculation="Wilder RSI, 14-period",
            interpretation=f"RSI is {value:.1f}, {reading}",
            source=source, data_status=status, observed_at=observed,
        ))

    def _macd_evidence(self, chain, s, source, status, observed) -> None:
        hist, prev = s.get("macd_hist"), s.get("macd_hist_prev")
        if hist is None:
            chain.note_gap("MACD unavailable")
            return
        fresh_cross = (
            prev is not None and np.sign(hist) != np.sign(prev) and hist != 0
        )
        stance = Stance.POSITIVE if hist > 0 else Stance.NEGATIVE
        descriptor = "bullish" if hist > 0 else "bearish"
        chain.add(EvidenceItem(
            metric="MACD histogram", value=round(hist, 4), stance=stance,
            weight=1.8 if fresh_cross else 1.2,
            calculation=f"MACD {s.get('macd')} - signal {s.get('macd_signal')}",
            interpretation=(
                f"MACD crossed {descriptor} on the latest bar"
                if fresh_cross else
                f"MACD is {descriptor} but the crossover is not new"
            ),
            source=source, data_status=status, observed_at=observed,
        ))

    def _volume_evidence(self, chain, s, source, status, observed) -> None:
        ratio = s.get("volume_ratio_20")
        if ratio is None:
            chain.note_gap("volume unavailable - conclusions are price-only")
            chain.limit(
                "No volume data: breakout and confirmation evidence is absent, "
                "which materially weakens any breakout reading."
            )
            return
        if ratio >= 1.5:
            stance, reading = Stance.POSITIVE, (
                f"{ratio:.2f}x its 20-day average - participation is elevated"
            )
        elif ratio <= 0.6:
            stance, reading = Stance.NEGATIVE, (
                f"{ratio:.2f}x its 20-day average - thin participation, so any "
                "price move carries less weight"
            )
        else:
            stance, reading = Stance.NEUTRAL, f"{ratio:.2f}x its 20-day average"
        chain.add(EvidenceItem(
            metric="Volume vs 20-day average", value=round(ratio, 2),
            stance=stance, weight=1.3,
            calculation="today's volume / mean(volume, previous 20 bars)",
            interpretation=f"volume is {reading}",
            unit="x", source=source, data_status=status, observed_at=observed,
        ))

    def _volatility_evidence(self, chain, s, source, status, observed) -> None:
        atr_pct = s.get("atr_pct")
        if atr_pct is None:
            chain.note_gap("ATR unavailable")
            return
        if atr_pct >= 4.0:
            stance, reading = Stance.NEGATIVE, (
                "high - stops must be wider, which shrinks position size for a "
                "given rupee risk"
            )
        elif atr_pct <= 1.2:
            stance, reading = Stance.NEUTRAL, (
                "low - compressed ranges often precede expansion in either "
                "direction"
            )
        else:
            stance, reading = Stance.NEUTRAL, "normal"
        chain.add(EvidenceItem(
            metric="ATR(14) as % of price", value=round(atr_pct, 2),
            stance=stance, weight=1.0, unit="%",
            calculation=f"ATR {s.get('atr_14')} / close x 100",
            interpretation=f"daily range is {atr_pct:.2f}% of price - {reading}",
            source=source, data_status=status, observed_at=observed,
        ))

    def _range_evidence(self, chain, s, source, status, observed) -> None:
        from_high = s.get("pct_from_52w_high")
        if from_high is None:
            chain.note_gap("52-week range unavailable")
            return
        if from_high >= -2:
            stance, reading = Stance.POSITIVE, "at or near its 52-week high"
        elif from_high <= -30:
            stance, reading = Stance.NEGATIVE, (
                f"{abs(from_high):.1f}% below its 52-week high"
            )
        else:
            stance, reading = Stance.NEUTRAL, (
                f"{abs(from_high):.1f}% below its 52-week high"
            )
        chain.add(EvidenceItem(
            metric="Distance from 52-week high", value=round(from_high, 2),
            stance=stance, weight=1.0, unit="%",
            calculation="close / rolling 252-bar max - 1",
            interpretation=f"price is {reading}",
            source=source, data_status=status, observed_at=observed,
        ))

    def _supertrend_evidence(self, chain, close, s, source, status, observed) -> None:
        direction = s.get("supertrend_dir")
        level = s.get("supertrend")
        if direction is None or level is None:
            chain.note_gap("Supertrend unavailable")
            return
        up = direction > 0
        chain.add(EvidenceItem(
            metric="Supertrend(10, 3)", value=round(level, 2),
            stance=Stance.POSITIVE if up else Stance.NEGATIVE, weight=1.2,
            calculation=f"close {close:.2f} vs supertrend line {level:.2f}",
            interpretation=(
                f"supertrend is in an up-phase with the line at {level:.2f} "
                "acting as trailing support"
                if up else
                f"supertrend is in a down-phase with the line at {level:.2f} "
                "acting as trailing resistance"
            ),
            unit="INR", source=source, data_status=status, observed_at=observed,
        ))

    def _level_evidence(self, chain, close, levels, source, status, observed) -> None:
        if not levels:
            chain.note_gap("no confirmed swing levels in the window")
            return
        resistance = [
            l for l in levels if l["price"] > close  # noqa: E741
        ]
        support = [l for l in levels if l["price"] <= close]  # noqa: E741
        nearest_res = min(resistance, key=lambda l: l["price"]) if resistance else None  # noqa: E741
        nearest_sup = max(support, key=lambda l: l["price"]) if support else None  # noqa: E741

        if nearest_res:
            distance = (nearest_res["price"] / close - 1.0) * 100.0
            chain.add(EvidenceItem(
                metric="Nearest resistance", value=nearest_res["price"],
                stance=Stance.NEGATIVE if distance < 2.0 else Stance.NEUTRAL,
                weight=1.4 if distance < 2.0 else 0.8, unit="INR",
                calculation=(
                    f"{nearest_res['touches']} swing touches clustered at "
                    f"{nearest_res['price']}, last touched "
                    f"{nearest_res['bars_since_last_touch']} bars ago"
                ),
                interpretation=(
                    f"resistance sits {distance:.2f}% overhead at "
                    f"{nearest_res['price']} (strength {nearest_res['strength']}/100)"
                ),
                source=source, data_status=status, observed_at=observed,
            ))
        if nearest_sup:
            distance = (1.0 - nearest_sup["price"] / close) * 100.0
            chain.add(EvidenceItem(
                metric="Nearest support", value=nearest_sup["price"],
                stance=Stance.POSITIVE if distance < 3.0 else Stance.NEUTRAL,
                weight=1.2, unit="INR",
                calculation=(
                    f"{nearest_sup['touches']} swing touches clustered at "
                    f"{nearest_sup['price']}"
                ),
                interpretation=(
                    f"support sits {distance:.2f}% below at "
                    f"{nearest_sup['price']} (strength {nearest_sup['strength']}/100)"
                ),
                source=source, data_status=status, observed_at=observed,
            ))

    # ------------------------------------------------------------------

    @staticmethod
    def _regime(s: Dict[str, Optional[float]], chain: EvidenceChain) -> Dict[str, Any]:
        """Market regime with the reasons that produced it."""
        reasons: List[str] = []
        adx_value = s.get("adx_14") or 0.0
        atr_pct = s.get("atr_pct") or 0.0
        score = chain.score

        if score is None:
            return {"regime": "INSUFFICIENT_DATA", "reasons":
                    ["not enough scored evidence"]}

        if atr_pct >= 4.5:
            reasons.append(f"ATR is {atr_pct:.1f}% of price (>= 4.5%)")
            regime = "HIGH_VOLATILITY"
        elif adx_value >= 25 and score >= 65:
            reasons.append(f"ADX {adx_value:.0f} >= 25 with technical score {score}")
            regime = "STRONG_BULLISH"
        elif adx_value >= 25 and score <= 35:
            reasons.append(f"ADX {adx_value:.0f} >= 25 with technical score {score}")
            regime = "STRONG_BEARISH"
        elif score >= 60:
            reasons.append(f"technical score {score} >= 60 without a strong ADX")
            regime = "BULLISH"
        elif score <= 40:
            reasons.append(f"technical score {score} <= 40")
            regime = "BEARISH"
        else:
            reasons.append(f"technical score {score} sits between 40 and 60")
            regime = "NEUTRAL"

        if adx_value < 20:
            reasons.append(f"ADX {adx_value:.0f} < 20 signals no established trend")
        return {"regime": regime, "reasons": reasons,
                "inputs": {"adx_14": s.get("adx_14"), "atr_pct": s.get("atr_pct"),
                           "technical_score": score}}


technical_analysis_service = TechnicalAnalysisService()
