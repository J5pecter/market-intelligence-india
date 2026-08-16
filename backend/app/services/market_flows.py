"""Flow analytics built on the official exchange record.

These are the analyses that need the *exchange's* data rather than a price
feed, which is why they could not exist before the archive adapters landed.

Delivery percentage
-------------------
The share of a session's traded volume that actually settled into demat
accounts instead of being squared off intraday. Two stocks can print identical
candles and mean opposite things: +6% on 78% delivery is someone taking stock
off the market, +6% on 14% delivery is intraday churn that frequently
round-trips the next session. No OHLC series can separate those, and no free
vendor publishes the split - only the exchange does.

The comparison that matters is a stock against *its own* usual delivery, not
against the market. Utilities habitually deliver 70%+ and index heavyweights
30-40%; judging both against one threshold just re-discovers the sector. Where
this module has no history for a symbol it says so rather than falling back on
a market-wide number and pretending it means something.

Open-interest buildup
---------------------
The standard four-way read of price change against OI change. Stated plainly:
it describes what positions did, not what price will do next. A long buildup is
evidence that new money took the long side, and nothing more - it is routinely
followed by a reversal when the crowd is offside.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence

from app.core.data_quality import DataStatus, Sourced
from app.providers.base import ProviderError
from app.providers.registry import registry
from app.services.evidence import EvidenceChain, EvidenceItem, Stance

logger = logging.getLogger(__name__)

#: Below this many observations a percentile is noise dressed up as a statistic.
MIN_HISTORY_FOR_PERCENTILE = 20


class DeliveryRegime(str, Enum):
    ACCUMULATION = "ACCUMULATION"        # delivery well above the stock's norm
    ELEVATED = "ELEVATED"
    NORMAL = "NORMAL"
    CHURN = "CHURN"                      # delivery well below the stock's norm
    UNKNOWN = "UNKNOWN"                  # not enough history to judge


class OiBuildup(str, Enum):
    LONG_BUILDUP = "LONG_BUILDUP"          # price up, OI up
    SHORT_BUILDUP = "SHORT_BUILDUP"        # price down, OI up
    SHORT_COVERING = "SHORT_COVERING"      # price up, OI down
    LONG_UNWINDING = "LONG_UNWINDING"      # price down, OI down
    INDETERMINATE = "INDETERMINATE"


@dataclass
class DeliveryReading:
    symbol: str
    session_date: str
    delivery_pct: Optional[float]
    traded_quantity: Optional[int]
    deliverable_quantity: Optional[int]
    regime: DeliveryRegime = DeliveryRegime.UNKNOWN
    median_pct: Optional[float] = None
    percentile: Optional[float] = None
    observations: int = 0
    market_median_pct: Optional[float] = None
    interpretation: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "session_date": self.session_date,
            "delivery_pct": self.delivery_pct,
            "traded_quantity": self.traded_quantity,
            "deliverable_quantity": self.deliverable_quantity,
            "regime": self.regime.value,
            "own_median_pct": self.median_pct,
            "percentile_vs_own_history": self.percentile,
            "observations": self.observations,
            "market_median_pct": self.market_median_pct,
            "interpretation": self.interpretation,
        }


# --------------------------------------------------------------------------
# delivery
# --------------------------------------------------------------------------


def delivery_snapshot(on: Optional[date] = None) -> Sourced[List[Dict[str, Any]]]:
    """The whole market's delivery report for one session."""
    return registry.fetch("delivery", on=on)


def analyse_delivery(
    symbol: str,
    on: Optional[date] = None,
    history: Optional[Sequence[float]] = None,
) -> tuple[Optional[DeliveryReading], Sourced[Any]]:
    """Read one symbol's delivery against its own history.

    `history` is the stock's prior delivery percentages, most recent last.
    Callers that have stored them pass them in; without them this reports
    UNKNOWN rather than inventing a baseline.
    """
    env = delivery_snapshot(on)
    rows = env.value or []
    if not rows:
        return None, env

    row = next((r for r in rows
                if (r.get("symbol") or "").upper() == symbol.upper()
                and (r.get("series") or "EQ").upper() in ("EQ", "BE")), None)
    if row is None:
        return None, env

    market = [r["delivery_pct"] for r in rows
              if r.get("delivery_pct") is not None
              and (r.get("series") or "").upper() == "EQ"]
    market_median = round(statistics.median(market), 2) if market else None

    reading = DeliveryReading(
        symbol=symbol.upper(),
        session_date=row.get("session_date") or "",
        delivery_pct=row.get("delivery_pct"),
        traded_quantity=row.get("traded_quantity"),
        deliverable_quantity=row.get("deliverable_quantity"),
        market_median_pct=market_median,
    )

    hist = [h for h in (history or []) if h is not None]
    reading.observations = len(hist)
    current = reading.delivery_pct

    if current is None:
        reading.interpretation = "The exchange published no delivery figure for this scrip."
        return reading, env

    if len(hist) < MIN_HISTORY_FOR_PERCENTILE:
        reading.regime = DeliveryRegime.UNKNOWN
        reading.interpretation = (
            f"Delivery was {current}% this session against a market median of "
            f"{market_median}%. Only {len(hist)} prior observations are stored "
            f"for {symbol.upper()}, short of the {MIN_HISTORY_FOR_PERCENTILE} "
            "needed to say whether that is normal for this stock. Sector habits "
            "vary far too much for the market median to stand in as a baseline."
        )
        return reading, env

    reading.median_pct = round(statistics.median(hist), 2)
    below = sum(1 for h in hist if h < current)
    reading.percentile = round(100.0 * below / len(hist), 1)

    if reading.percentile >= 90:
        reading.regime = DeliveryRegime.ACCUMULATION
        verdict = ("far above its own norm - a larger share of volume than "
                   "usual left the market as stock")
    elif reading.percentile >= 70:
        reading.regime = DeliveryRegime.ELEVATED
        verdict = "above its own norm"
    elif reading.percentile <= 10:
        reading.regime = DeliveryRegime.CHURN
        verdict = ("far below its own norm - most of the volume was squared "
                   "off intraday rather than settled")
    else:
        reading.regime = DeliveryRegime.NORMAL
        verdict = "in line with its own norm"

    reading.interpretation = (
        f"Delivery was {current}% against a {len(hist)}-session median of "
        f"{reading.median_pct}% for {symbol.upper()}, the "
        f"{reading.percentile:.0f}th percentile of its own history - {verdict}. "
        f"Market median was {market_median}%. This describes settlement, not "
        "direction: heavy delivery into a fall is distribution just as much as "
        "heavy delivery into a rally is accumulation."
    )
    return reading, env


def delivery_evidence(reading: DeliveryReading, env: Sourced[Any]) -> EvidenceChain:
    items: List[EvidenceItem] = []
    limitations: List[str] = []

    stance = {
        DeliveryRegime.ACCUMULATION: Stance.POSITIVE,
        DeliveryRegime.ELEVATED: Stance.POSITIVE,
        DeliveryRegime.CHURN: Stance.NEGATIVE,
        DeliveryRegime.NORMAL: Stance.NEUTRAL,
        DeliveryRegime.UNKNOWN: Stance.UNKNOWN,
    }[reading.regime]

    if reading.delivery_pct is not None:
        items.append(EvidenceItem(
            metric="Delivery percentage",
            value=reading.delivery_pct,
            stance=stance,
            weight=1.5,
            calculation=(
                f"deliverable {reading.deliverable_quantity:,} / traded "
                f"{reading.traded_quantity:,} x 100"
                if reading.deliverable_quantity and reading.traded_quantity
                else "as published by the exchange"
            ),
            interpretation=reading.interpretation,
            source=env.source_name,
            source_url=env.source_url,
            observed_at=env.observed_at,
            data_status=env.status.value,
            unit="%",
        ))

    if reading.market_median_pct is not None:
        items.append(EvidenceItem(
            metric="Market median delivery",
            value=reading.market_median_pct,
            stance=Stance.NEUTRAL,
            weight=0.5,
            calculation="median delivery % across all EQ-series scrips this session",
            interpretation=(
                "Context for the session as a whole. A market-wide delivery "
                "spike usually reflects index rebalancing or expiry, not "
                "stock-specific conviction."
            ),
            source=env.source_name,
            observed_at=env.observed_at,
            data_status=env.status.value,
            unit="%",
        ))

    if reading.regime is DeliveryRegime.UNKNOWN:
        limitations.append(
            f"Only {reading.observations} prior sessions are stored for this "
            f"symbol; {MIN_HISTORY_FOR_PERCENTILE} are needed before a "
            "percentile means anything."
        )
    limitations.append(
        "Delivery percentage is settlement data, not intent. It cannot "
        "distinguish an institution accumulating from a promoter pledging."
    )

    return EvidenceChain(
        dimension="DELIVERY",
        score=reading.percentile,
        stance=stance,
        summary=reading.interpretation,
        items=items,
        limitations=limitations,
        data_gaps=([] if reading.delivery_pct is not None
                   else ["The exchange published no delivery figure for this scrip."]),
        methodology_ref="/methodology#delivery",
    )


# --------------------------------------------------------------------------
# open interest
# --------------------------------------------------------------------------


def classify_buildup(price_change_pct: Optional[float],
                     oi_change_pct: Optional[float],
                     price_threshold: float = 0.1,
                     oi_threshold: float = 1.0) -> OiBuildup:
    """The four-way price/OI read.

    Both thresholds exist so that a flat session is not forced into one of the
    four corners: a 0.02% price move with 0.3% more OI is noise, and labelling
    it a long buildup would manufacture a signal out of rounding.
    """
    if price_change_pct is None or oi_change_pct is None:
        return OiBuildup.INDETERMINATE
    if abs(price_change_pct) < price_threshold or abs(oi_change_pct) < oi_threshold:
        return OiBuildup.INDETERMINATE
    if price_change_pct > 0:
        return OiBuildup.LONG_BUILDUP if oi_change_pct > 0 else OiBuildup.SHORT_COVERING
    return OiBuildup.SHORT_BUILDUP if oi_change_pct > 0 else OiBuildup.LONG_UNWINDING


BUILDUP_MEANING = {
    OiBuildup.LONG_BUILDUP: (
        "Price rose while open interest grew: new money took the long side. "
        "Evidence of fresh positioning, not of where price goes next."
    ),
    OiBuildup.SHORT_BUILDUP: (
        "Price fell while open interest grew: new money took the short side. "
        "Crowded shorts are also the fuel for a squeeze."
    ),
    OiBuildup.SHORT_COVERING: (
        "Price rose while open interest shrank: existing shorts bought back "
        "rather than new longs arriving. Rallies on covering alone tend to "
        "stall once the shorts are out."
    ),
    OiBuildup.LONG_UNWINDING: (
        "Price fell while open interest shrank: existing longs sold out. "
        "Positions are being closed, not reversed."
    ),
    OiBuildup.INDETERMINATE: (
        "Price or open interest moved too little to read anything into the "
        "combination."
    ),
}


def buildup_evidence(symbol: str, price_change_pct: Optional[float],
                     oi_change_pct: Optional[float],
                     env: Optional[Sourced[Any]] = None) -> EvidenceChain:
    state = classify_buildup(price_change_pct, oi_change_pct)
    stance = {
        OiBuildup.LONG_BUILDUP: Stance.POSITIVE,
        OiBuildup.SHORT_COVERING: Stance.NEUTRAL,
        OiBuildup.SHORT_BUILDUP: Stance.NEGATIVE,
        OiBuildup.LONG_UNWINDING: Stance.NEGATIVE,
        OiBuildup.INDETERMINATE: Stance.UNKNOWN,
    }[state]

    items = []
    if state is not OiBuildup.INDETERMINATE:
        items.append(EvidenceItem(
            metric="Price / OI buildup",
            value=state.value,
            stance=stance,
            weight=1.2,
            calculation=(
                f"price {price_change_pct:+.2f}% with open interest "
                f"{oi_change_pct:+.2f}%"
            ),
            interpretation=BUILDUP_MEANING[state],
            source=env.source_name if env else "derivatives feed",
            observed_at=env.observed_at if env else None,
            data_status=env.status.value if env else None,
        ))

    return EvidenceChain(
        dimension="OPEN_INTEREST",
        stance=stance,
        summary=f"{symbol.upper()}: {state.value.replace('_', ' ').lower()}. "
                + BUILDUP_MEANING[state],
        items=items,
        limitations=[
            "Open interest describes positioning that has already happened. "
            "It carries no probability about the next session.",
            "Stock futures OI mixes hedges, arbitrage and directional bets, "
            "which the aggregate number cannot separate.",
        ],
        methodology_ref="/methodology#open-interest",
    )


# --------------------------------------------------------------------------
# breadth from the bhavcopy
# --------------------------------------------------------------------------


def market_breadth(on: Optional[date] = None) -> tuple[Dict[str, Any], Sourced[Any]]:
    """Advance/decline and distribution stats from the official bhavcopy."""
    env = registry.fetch("bhavcopy", on=on)
    rows = [r for r in (env.value or [])
            if (r.get("series") or "").upper() in ("EQ", "A", "B")]
    if not rows:
        return {}, env

    changes: List[float] = []
    advances = declines = unchanged = 0
    for r in rows:
        close, prev = r.get("close"), r.get("previous_close")
        if close is None or not prev:
            continue
        pct = (close - prev) / prev * 100.0
        changes.append(pct)
        if pct > 0.0:
            advances += 1
        elif pct < 0.0:
            declines += 1
        else:
            unchanged += 1

    if not changes:
        return {}, env

    changes.sort()
    total = len(changes)
    turnover = sum(r.get("turnover") or 0 for r in rows)

    breadth = {
        "session_date": rows[0].get("session_date"),
        "scrips_counted": total,
        "advances": advances,
        "declines": declines,
        "unchanged": unchanged,
        "advance_decline_ratio": round(advances / declines, 3) if declines else None,
        "median_change_pct": round(statistics.median(changes), 2),
        "mean_change_pct": round(statistics.fmean(changes), 2),
        "pct_up_more_than_2": round(
            100.0 * sum(1 for c in changes if c > 2.0) / total, 1),
        "pct_down_more_than_2": round(
            100.0 * sum(1 for c in changes if c < -2.0) / total, 1),
        "total_turnover": round(turnover, 2) if turnover else None,
        # The median is the honest headline: an index can rise on five names
        # while most of the market falls, and the mean hides that.
        "interpretation": _breadth_note(advances, declines, changes),
    }
    return breadth, env


def _breadth_note(advances: int, declines: int, changes: List[float]) -> str:
    median = statistics.median(changes)
    ratio = (advances / declines) if declines else float("inf")
    if ratio >= 2.0:
        tone = "broad participation on the upside"
    elif ratio >= 1.2:
        tone = "more advances than declines"
    elif ratio <= 0.5:
        tone = "broad selling"
    elif ratio <= 0.83:
        tone = "more declines than advances"
    else:
        tone = "an even split"
    return (
        f"{advances} advancing against {declines} declining - {tone}. "
        f"The median scrip moved {median:+.2f}%, which is what the typical "
        "stock did regardless of what the headline index printed."
    )


# --------------------------------------------------------------------------
# institutional deals
# --------------------------------------------------------------------------


def deal_flow(on: Optional[date] = None,
              kind: str = "bulk") -> tuple[List[Dict[str, Any]], Sourced[Any]]:
    """Net buy/sell per symbol from a deal register.

    Both legs of a deal are reported, so gross quantity double-counts. Netting
    buys against sells per symbol is the only figure that means anything.
    """
    env = registry.fetch(f"{kind}_deals", on=on)
    rows = env.value or []
    if not rows:
        return [], env

    agg: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        symbol = (r.get("symbol") or "").upper()
        if not symbol:
            continue
        entry = agg.setdefault(symbol, {
            "symbol": symbol,
            "security_name": r.get("security_name"),
            "buy_quantity": 0, "sell_quantity": 0,
            "buy_value": 0.0, "sell_value": 0.0,
            "buyers": [], "sellers": [], "deal_count": 0,
        })
        entry["deal_count"] += 1
        qty, value = r.get("quantity") or 0, r.get("value") or 0.0
        client = r.get("client_name")
        if r.get("buy_sell", "").startswith("B"):
            entry["buy_quantity"] += qty
            entry["buy_value"] += value
            if client:
                entry["buyers"].append(client)
        else:
            entry["sell_quantity"] += qty
            entry["sell_value"] += value
            if client:
                entry["sellers"].append(client)

    out = []
    for entry in agg.values():
        entry["net_quantity"] = entry["buy_quantity"] - entry["sell_quantity"]
        entry["net_value"] = round(entry["buy_value"] - entry["sell_value"], 2)
        entry["gross_value"] = round(entry["buy_value"] + entry["sell_value"], 2)
        entry["direction"] = (
            "NET_BUY" if entry["net_quantity"] > 0
            else "NET_SELL" if entry["net_quantity"] < 0 else "MATCHED"
        )
        # A matched deal is one party selling to another through the same
        # register - it moves ownership without moving net demand.
        entry["note"] = (
            "Buy and sell legs match, so this is a transfer between two "
            "reported parties rather than net accumulation."
            if entry["direction"] == "MATCHED" else
            f"Reported {entry['direction'].replace('_', ' ').lower()} of "
            f"{abs(entry['net_quantity']):,} shares across "
            f"{entry['deal_count']} disclosed deal(s)."
        )
        out.append(entry)

    out.sort(key=lambda e: abs(e["net_value"]), reverse=True)
    return out, env
