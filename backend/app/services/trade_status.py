"""Trade status engine and setup arithmetic.

Two responsibilities:

1. Classify where a published setup currently stands (`evaluate_status`). The
   UI must never show a permanent "BUY" - the badge is derived from live price
   against the published levels, every time it is rendered.

2. Compute the numbers on the card - achieved, potential, risk/reward - with
   the formula attached, so a reader can check the arithmetic.

Formula conventions (these match the reference cards this platform was
specified against, and are stated on every payload):

    achieved_pct          = (LTP - entry_reference) / entry_reference * 100
    potential_from_entry  = (target - entry_reference) / entry_reference * 100
    potential_from_ltp    = (target - LTP) / LTP * 100
    risk                  = |entry_reference - stop_loss|
    reward                = |target - entry_reference|
    risk_reward           = reward / risk

`entry_reference` is the *worst* end of the entry range for the direction
traded: the top of the range for a long, the bottom for a short. That is the
conservative choice - it never flatters the setup.

Equity cards conventionally show "potential expected" (from entry) while
option cards show "potential left" (from the current premium). Both are always
computed and both are returned; only the emphasis differs.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class TradeStatus(str, Enum):
    NOT_ACTIVATED = "NOT_ACTIVATED"
    WITHIN_ENTRY = "WITHIN_ENTRY"
    ABOVE_ENTRY = "ABOVE_ENTRY"
    BELOW_ENTRY = "BELOW_ENTRY"
    TARGET_IN_PROGRESS = "TARGET_IN_PROGRESS"
    TARGET_ACHIEVED = "TARGET_ACHIEVED"
    STOP_LOSS_TRIGGERED = "STOP_LOSS_TRIGGERED"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"
    UNKNOWN = "UNKNOWN"


STATUS_LABELS = {
    TradeStatus.NOT_ACTIVATED: "Not activated",
    TradeStatus.WITHIN_ENTRY: "Within entry range",
    TradeStatus.ABOVE_ENTRY: "Above entry range",
    TradeStatus.BELOW_ENTRY: "Below entry range",
    TradeStatus.TARGET_IN_PROGRESS: "Moving toward target",
    TradeStatus.TARGET_ACHIEVED: "Target reached",
    TradeStatus.STOP_LOSS_TRIGGERED: "Stop loss reached",
    TradeStatus.EXPIRED: "Expired",
    TradeStatus.INVALIDATED: "Invalidated",
    TradeStatus.UNKNOWN: "Status unavailable",
}


@dataclass
class SetupLevels:
    side: str                       # BUY | SELL
    entry_min: Optional[float]
    entry_max: Optional[float]
    stop_loss: Optional[float] = None
    target_1: Optional[float] = None
    target_2: Optional[float] = None
    target_3: Optional[float] = None
    valid_until: Optional[datetime] = None

    @property
    def is_long(self) -> bool:
        return self.side.upper() != "SELL"

    @property
    def entry_reference(self) -> Optional[float]:
        """Worst realistic fill for the direction - the conservative anchor."""
        if self.entry_min is None and self.entry_max is None:
            return None
        if self.entry_min is None:
            return self.entry_max
        if self.entry_max is None:
            return self.entry_min
        return max(self.entry_min, self.entry_max) if self.is_long else \
            min(self.entry_min, self.entry_max)

    @property
    def entry_low(self) -> Optional[float]:
        values = [v for v in (self.entry_min, self.entry_max) if v is not None]
        return min(values) if values else None

    @property
    def entry_high(self) -> Optional[float]:
        values = [v for v in (self.entry_min, self.entry_max) if v is not None]
        return max(values) if values else None

    @property
    def targets(self) -> List[float]:
        return [t for t in (self.target_1, self.target_2, self.target_3)
                if t is not None]

    @property
    def final_target(self) -> Optional[float]:
        targets = self.targets
        if not targets:
            return None
        return max(targets) if self.is_long else min(targets)


@dataclass
class StatusEvaluation:
    status: TradeStatus
    label: str
    reason: str
    reference_price: Optional[float]
    achieved_pct: Optional[float] = None
    potential_from_entry_pct: Optional[float] = None
    potential_from_ltp_pct: Optional[float] = None
    risk_per_unit: Optional[float] = None
    reward_per_unit: Optional[float] = None
    risk_reward: Optional[float] = None
    downside_to_stop_pct: Optional[float] = None
    target_progress_pct: Optional[float] = None
    targets: List[Dict[str, Any]] = field(default_factory=list)
    formulas: Dict[str, str] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    evaluated_at: datetime = field(
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        payload["evaluated_at"] = self.evaluated_at.isoformat()
        return payload


def evaluate_status(
    levels: SetupLevels,
    ltp: Optional[float],
    now: Optional[datetime] = None,
    price_is_stale: bool = False,
    high_since_publication: Optional[float] = None,
    low_since_publication: Optional[float] = None,
    manually_invalidated: bool = False,
) -> StatusEvaluation:
    """Classify a setup and compute its numbers.

    `high_since_publication` / `low_since_publication` matter: a setup whose
    target was touched intraday and then retraced is TARGET_ACHIEVED, not
    TARGET_IN_PROGRESS. Without them we can only judge on the last price, and
    the evaluation says so in `warnings`.
    """
    now = now or datetime.now(tz=timezone.utc)
    warnings: List[str] = []
    formulas = {
        "achieved_pct": "(LTP - entry_reference) / entry_reference x 100",
        "potential_from_entry_pct":
            "(target - entry_reference) / entry_reference x 100",
        "potential_from_ltp_pct": "(target - LTP) / LTP x 100",
        "risk_per_unit": "|entry_reference - stop_loss|",
        "reward_per_unit": "|final_target - entry_reference|",
        "risk_reward": "reward_per_unit / risk_per_unit",
        "entry_reference": (
            "top of the entry range for a long, bottom for a short - the "
            "conservative fill assumption"
        ),
    }

    entry_ref = levels.entry_reference
    is_long = levels.is_long

    if manually_invalidated:
        return StatusEvaluation(
            TradeStatus.INVALIDATED, STATUS_LABELS[TradeStatus.INVALIDATED],
            "An operator marked the underlying thesis invalid.",
            ltp, formulas=formulas, warnings=warnings,
        )

    if ltp is None:
        warnings.append("No current price is available for this instrument.")
        return StatusEvaluation(
            TradeStatus.UNKNOWN, STATUS_LABELS[TradeStatus.UNKNOWN],
            "Live price unavailable, so the setup cannot be classified.",
            None, formulas=formulas, warnings=warnings,
        )

    if price_is_stale:
        warnings.append(
            "The price used is stale. The status shown reflects the last "
            "successful update, not the live market."
        )

    if entry_ref is None:
        warnings.append("No entry level was published for this setup.")
        return StatusEvaluation(
            TradeStatus.UNKNOWN, STATUS_LABELS[TradeStatus.UNKNOWN],
            "No entry level published, so progress cannot be measured.",
            ltp, formulas=formulas, warnings=warnings,
        )
    if entry_ref == 0:
        warnings.append("Entry reference is zero - percentages are undefined.")
        return StatusEvaluation(
            TradeStatus.UNKNOWN, STATUS_LABELS[TradeStatus.UNKNOWN],
            "Entry level is zero.", ltp, formulas=formulas, warnings=warnings,
        )

    # --- arithmetic ------------------------------------------------------
    achieved = (ltp - entry_ref) / entry_ref * 100.0
    if not is_long:
        achieved = -achieved

    final_target = levels.final_target
    potential_entry = potential_ltp = None
    if final_target is not None:
        potential_entry = (final_target - entry_ref) / entry_ref * 100.0
        if ltp:
            potential_ltp = (final_target - ltp) / ltp * 100.0
        if not is_long:
            potential_entry = -potential_entry if potential_entry is not None else None
            potential_ltp = -potential_ltp if potential_ltp is not None else None

    risk = reward = rr = None
    if levels.stop_loss is not None:
        risk = abs(entry_ref - levels.stop_loss)
        if risk == 0:
            warnings.append(
                "Stop loss equals the entry reference: risk per unit is zero, "
                "so risk/reward is undefined rather than infinite."
            )
            risk = None
    if final_target is not None:
        reward = abs(final_target - entry_ref)
    if risk and reward is not None:
        rr = round(reward / risk, 2)

    downside_pct = (
        abs(levels.stop_loss - entry_ref) / entry_ref * 100.0
        if levels.stop_loss is not None else None
    )

    progress = None
    if final_target is not None and final_target != entry_ref:
        progress = (ltp - entry_ref) / (final_target - entry_ref) * 100.0
        progress = round(max(-999.0, min(999.0, progress)), 2)

    target_rows = _target_rows(levels, entry_ref, ltp, risk, is_long)

    # --- classification --------------------------------------------------
    status, reason = _classify(
        levels, ltp, is_long, entry_ref, now,
        high_since_publication, low_since_publication, warnings,
    )

    return StatusEvaluation(
        status=status,
        label=STATUS_LABELS[status],
        reason=reason,
        reference_price=round(ltp, 4),
        achieved_pct=round(achieved, 2),
        potential_from_entry_pct=(
            round(potential_entry, 2) if potential_entry is not None else None
        ),
        potential_from_ltp_pct=(
            round(potential_ltp, 2) if potential_ltp is not None else None
        ),
        risk_per_unit=round(risk, 4) if risk else None,
        reward_per_unit=round(reward, 4) if reward is not None else None,
        risk_reward=rr,
        downside_to_stop_pct=round(downside_pct, 2) if downside_pct is not None else None,
        target_progress_pct=progress,
        targets=target_rows,
        formulas=formulas,
        warnings=warnings,
    )


def _classify(
    levels: SetupLevels, ltp: float, is_long: bool, entry_ref: float,
    now: datetime, high_since: Optional[float], low_since: Optional[float],
    warnings: List[str],
) -> tuple[TradeStatus, str]:
    """Order matters: stop and target are checked before range membership,
    because a hit stop is a terminal state regardless of where price sits now."""

    if high_since is None and low_since is None:
        warnings.append(
            "Target and stop are judged on the current price only - the "
            "high/low path since publication was not supplied, so a level "
            "touched intraday and since retraced would be missed."
        )

    stop = levels.stop_loss
    final_target = levels.final_target
    extreme_low = low_since if low_since is not None else ltp
    extreme_high = high_since if high_since is not None else ltp

    if stop is not None:
        hit = extreme_low <= stop if is_long else extreme_high >= stop
        if hit:
            return TradeStatus.STOP_LOSS_TRIGGERED, (
                f"Price reached the stop at {stop:g} "
                f"({'low' if is_long else 'high'} seen: "
                f"{extreme_low if is_long else extreme_high:g})."
            )

    if final_target is not None:
        hit = extreme_high >= final_target if is_long else extreme_low <= final_target
        if hit:
            return TradeStatus.TARGET_ACHIEVED, (
                f"Price reached the final target at {final_target:g}."
            )

    if levels.valid_until is not None:
        valid_until = levels.valid_until
        if valid_until.tzinfo is None:
            valid_until = valid_until.replace(tzinfo=timezone.utc)
        if now > valid_until:
            return TradeStatus.EXPIRED, (
                f"The publication's validity window ended on "
                f"{valid_until.date().isoformat()}."
            )

    low, high = levels.entry_low, levels.entry_high
    if low is not None and high is not None:
        # Inclusive bounds: an LTP exactly on either edge is inside the range.
        if low <= ltp <= high:
            return TradeStatus.WITHIN_ENTRY, (
                f"Last price {ltp:g} is inside the published entry range "
                f"{low:g}-{high:g} (bounds inclusive)."
            )
        if is_long:
            if ltp > high:
                progressed = (
                    final_target is not None and ltp > entry_ref
                )
                if progressed:
                    return TradeStatus.TARGET_IN_PROGRESS, (
                        f"Price {ltp:g} is above the entry range and moving "
                        f"toward the target at {final_target:g}."
                    )
                return TradeStatus.ABOVE_ENTRY, (
                    f"Price {ltp:g} is above the entry range {low:g}-{high:g}; "
                    f"the published entry opportunity has passed."
                )
            return TradeStatus.NOT_ACTIVATED, (
                f"Price {ltp:g} has not yet entered the range {low:g}-{high:g}."
            )
        # short
        if ltp < low:
            if final_target is not None and ltp < entry_ref:
                return TradeStatus.TARGET_IN_PROGRESS, (
                    f"Price {ltp:g} is below the entry range and moving toward "
                    f"the target at {final_target:g}."
                )
            return TradeStatus.BELOW_ENTRY, (
                f"Price {ltp:g} is below the entry range {low:g}-{high:g}; the "
                f"published entry opportunity has passed."
            )
        return TradeStatus.NOT_ACTIVATED, (
            f"Price {ltp:g} has not yet entered the range {low:g}-{high:g}."
        )

    return TradeStatus.UNKNOWN, "Entry range is incomplete."


def _target_rows(levels: SetupLevels, entry_ref: float, ltp: float,
                 risk: Optional[float], is_long: bool) -> List[Dict[str, Any]]:
    """Multi-target ladder with per-target reward and R multiple."""
    rows: List[Dict[str, Any]] = []
    for index, target in enumerate(
        [levels.target_1, levels.target_2, levels.target_3], start=1
    ):
        if target is None:
            continue
        move = (target - entry_ref) if is_long else (entry_ref - target)
        rows.append({
            "index": index,
            "price": target,
            "return_from_entry_pct": round(move / entry_ref * 100.0, 2),
            "return_from_ltp_pct": round(
                ((target - ltp) if is_long else (ltp - target)) / ltp * 100.0, 2
            ) if ltp else None,
            "r_multiple": round(abs(move) / risk, 2) if risk else None,
            "reached": (ltp >= target) if is_long else (ltp <= target),
            "note": (
                "R multiple is reward divided by the per-unit risk implied by "
                "the published stop. It is not a probability."
            ),
        })
    return rows


# --------------------------------------------------------------------------
# Position sizing and the P&L simulator
# --------------------------------------------------------------------------


@dataclass
class BrokerageModel:
    """Indian equity/derivatives charges. Defaults are a *configurable
    assumption*, not your broker's actual rate card - override them."""

    brokerage_per_order: float = 20.0
    brokerage_pct: float = 0.0
    stt_pct_sell_delivery: float = 0.1
    stt_pct_sell_intraday: float = 0.025
    stt_pct_sell_options_premium: float = 0.1
    stt_pct_sell_futures: float = 0.02
    exchange_txn_pct: float = 0.00325
    sebi_charges_pct: float = 0.0001
    stamp_duty_pct_buy: float = 0.015
    gst_pct: float = 18.0
    source_note: str = (
        "Charges are modelled from published statutory rates and a flat "
        "brokerage assumption. They are indicative only - your contract note "
        "is the authority."
    )


def estimate_charges(
    buy_value: float, sell_value: float, segment: str = "EQUITY_DELIVERY",
    model: Optional[BrokerageModel] = None,
) -> Dict[str, float]:
    """Round-trip cost estimate with each component itemised."""
    m = model or BrokerageModel()
    turnover = buy_value + sell_value

    brokerage = 2 * m.brokerage_per_order + turnover * m.brokerage_pct / 100.0
    if segment == "EQUITY_DELIVERY":
        brokerage = turnover * m.brokerage_pct / 100.0  # many brokers: free delivery
        stt = sell_value * m.stt_pct_sell_delivery / 100.0
    elif segment == "EQUITY_INTRADAY":
        stt = sell_value * m.stt_pct_sell_intraday / 100.0
    elif segment == "OPTION":
        stt = sell_value * m.stt_pct_sell_options_premium / 100.0
    elif segment == "FUTURE":
        stt = sell_value * m.stt_pct_sell_futures / 100.0
    else:
        stt = 0.0

    exchange = turnover * m.exchange_txn_pct / 100.0
    sebi = turnover * m.sebi_charges_pct / 100.0
    stamp = buy_value * m.stamp_duty_pct_buy / 100.0
    gst = (brokerage + exchange + sebi) * m.gst_pct / 100.0
    total = brokerage + stt + exchange + sebi + stamp + gst

    return {
        "brokerage": round(brokerage, 2),
        "stt": round(stt, 2),
        "exchange_transaction": round(exchange, 2),
        "sebi_charges": round(sebi, 2),
        "stamp_duty": round(stamp, 2),
        "gst": round(gst, 2),
        "total": round(total, 2),
        "note": m.source_note,
    }


def position_size(
    capital: float, max_loss_pct: float, entry: float,
    stop_loss: float, lot_size: int = 1,
) -> Dict[str, Any]:
    """Quantity implied by a rupee risk budget.

    Assumptions are returned alongside the answer because they drive it
    entirely: the stop is assumed to fill at its stated price (gaps and
    slippage are not modelled here), and no leverage is applied.
    """
    problems: List[str] = []
    if capital <= 0:
        problems.append("capital must be positive")
    if entry <= 0:
        problems.append("entry must be positive")
    risk_per_unit = abs(entry - stop_loss)
    if risk_per_unit == 0:
        problems.append(
            "stop loss equals entry, so the per-unit risk is zero and no "
            "quantity can be derived from a risk budget"
        )
    if problems:
        return {"error": "; ".join(problems), "quantity": 0}

    max_rupee_risk = capital * max_loss_pct / 100.0
    raw_quantity = max_rupee_risk / risk_per_unit
    quantity = int(raw_quantity // lot_size) * lot_size if lot_size > 1 else int(raw_quantity)

    capital_deployed = quantity * entry
    over_capital = capital_deployed > capital

    return {
        "max_rupee_risk": round(max_rupee_risk, 2),
        "risk_per_unit": round(risk_per_unit, 4),
        "raw_quantity": round(raw_quantity, 4),
        "quantity": quantity,
        "lot_size": lot_size,
        "lots": quantity // lot_size if lot_size > 1 else None,
        "capital_deployed": round(capital_deployed, 2),
        "capital_deployed_pct": round(capital_deployed / capital * 100.0, 2),
        "exceeds_capital": over_capital,
        "assumptions": [
            f"Risk budget is {max_loss_pct}% of Rs {capital:,.0f}.",
            "The stop is assumed to fill at its stated price. Gaps and "
            "slippage are not modelled here and can make the realised loss "
            "larger than the budget.",
            "No leverage or margin benefit is assumed.",
            f"Quantity is rounded down to a multiple of the lot size ({lot_size})."
            if lot_size > 1 else "Quantity is rounded down to a whole share.",
        ],
        "warnings": (
            ["Required capital exceeds the capital supplied - reduce the "
             "position or widen the risk budget."] if over_capital else []
        ),
    }


def simulate_pnl(
    capital: float, entry: float, stop_loss: Optional[float],
    target: Optional[float], quantity: int, lot_size: int = 1,
    segment: str = "EQUITY_DELIVERY", side: str = "BUY",
    model: Optional[BrokerageModel] = None,
) -> Dict[str, Any]:
    """Bull / base / bear outcomes with charges. Explicitly not a forecast."""
    units = quantity * (lot_size if segment in ("OPTION", "FUTURE") else 1)
    capital_used = units * entry
    is_long = side.upper() == "BUY"

    def _outcome(exit_price: Optional[float], name: str,
                 description: str) -> Optional[Dict[str, Any]]:
        if exit_price is None:
            return None
        gross = (exit_price - entry) * units
        if not is_long:
            gross = -gross
        buy_value = units * (entry if is_long else exit_price)
        sell_value = units * (exit_price if is_long else entry)
        charges = estimate_charges(buy_value, sell_value, segment, model)
        net = gross - charges["total"]
        return {
            "scenario": name,
            "description": description,
            "exit_price": exit_price,
            "gross_pnl": round(gross, 2),
            "charges": charges,
            "net_pnl": round(net, 2),
            "return_on_capital_used_pct": round(net / capital_used * 100.0, 2)
            if capital_used else None,
            "return_on_total_capital_pct": round(net / capital * 100.0, 2)
            if capital else None,
        }

    base_price = entry
    scenarios = [
        _outcome(target, "Bull",
                 "Price reaches the published target. Not a probability - "
                 "simply the arithmetic if it happens."),
        _outcome(base_price, "Base",
                 "Price returns to the entry level and the position is closed "
                 "flat. Charges still apply, so the net result is negative."),
        _outcome(stop_loss, "Bear",
                 "Price reaches the stop. A gap through the stop would make "
                 "the realised loss larger than this."),
    ]
    scenarios = [s for s in scenarios if s is not None]

    breakeven = None
    if units and capital_used:
        round_trip = estimate_charges(capital_used, capital_used, segment, model)
        move_needed = round_trip["total"] / units
        breakeven = round(entry + move_needed if is_long else entry - move_needed, 4)

    return {
        "inputs": {
            "capital": capital, "entry": entry, "stop_loss": stop_loss,
            "target": target, "quantity": quantity, "lot_size": lot_size,
            "units": units, "segment": segment, "side": side,
        },
        "capital_used": round(capital_used, 2),
        "capital_used_pct": round(capital_used / capital * 100.0, 2)
        if capital else None,
        "breakeven_price": breakeven,
        "max_theoretical_loss": (
            round(abs(entry - stop_loss) * units, 2) if stop_loss is not None
            else (round(capital_used, 2) if segment == "OPTION" and is_long
                  else None)
        ),
        "max_theoretical_loss_note": (
            "For a long option, the premium paid is the maximum loss. For "
            "equity and futures the loss is bounded only by how far price "
            "moves, and a stop does not guarantee a fill at its price."
        ),
        "scenarios": scenarios,
        "disclaimer": (
            "These are arithmetic outcomes for the prices supplied. None of "
            "them is a prediction, and no probability is attached to any of "
            "them."
        ),
    }
