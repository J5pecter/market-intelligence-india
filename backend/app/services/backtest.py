"""Backtesting engine.

Design rules, enforced in code rather than promised in a docstring:

* **No look-ahead.** A signal computed from bar *i* can only be acted on at
  bar *i+1*'s open. `_execute` never fills at the price that generated the
  signal. Indicators are computed once over the whole series, which is safe
  because every indicator here is causal (backward-looking only).
* **Intrabar ambiguity is resolved pessimistically.** If a bar's range touches
  both the stop and the target, the stop is taken. Without tick data we cannot
  know which came first, and the optimistic assumption is the one that flatters
  a strategy into looking tradeable.
* **Costs are always applied.** Brokerage, statutory charges and slippage are
  deducted on both legs. A cost-free backtest is a marketing exercise.
* **Samples are separated.** In-sample and out-of-sample metrics are reported
  apart, and a walk-forward pass reports each fold.

The strategy DSL is intentionally small and declarative so it can be built in
the UI, serialised to the database, and audited later.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from app.services import indicators as ind
from app.services.trade_status import BrokerageModel, estimate_charges

METHODOLOGY = "/methodology#backtesting"
TRADING_DAYS = 252


# --------------------------------------------------------------------------
# Strategy definition
# --------------------------------------------------------------------------


@dataclass
class Condition:
    """`left op right`, where each side is a column name or a number.

    Columns come from indicators.compute_all, so anything the platform can
    chart it can also test.
    """

    left: str
    op: str                 # > < >= <= cross_above cross_below
    right: str | float

    def evaluate(self, frame: pd.DataFrame) -> pd.Series:
        left = _resolve(frame, self.left)
        right = _resolve(frame, self.right)

        if self.op == ">":
            return left > right
        if self.op == "<":
            return left < right
        if self.op == ">=":
            return left >= right
        if self.op == "<=":
            return left <= right
        if self.op == "cross_above":
            return (left > right) & (left.shift(1) <= right.shift(1))
        if self.op == "cross_below":
            return (left < right) & (left.shift(1) >= right.shift(1))
        raise ValueError(f"unsupported operator: {self.op}")


@dataclass
class StrategySpec:
    name: str = "Untitled strategy"
    direction: str = "LONG"                     # LONG | SHORT
    entry_conditions: List[Condition] = field(default_factory=list)
    entry_logic: str = "AND"                    # AND | OR
    exit_conditions: List[Condition] = field(default_factory=list)
    exit_logic: str = "OR"
    stop_loss_pct: Optional[float] = None
    target_pct: Optional[float] = None
    trailing_stop_pct: Optional[float] = None
    atr_stop_multiple: Optional[float] = None
    max_holding_bars: Optional[int] = 40
    position_size_pct: float = 100.0            # of equity per trade
    slippage_pct: float = 0.05
    segment: str = "EQUITY_DELIVERY"
    allow_pyramiding: bool = False

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "StrategySpec":
        return cls(
            name=payload.get("name", "Untitled strategy"),
            direction=payload.get("direction", "LONG").upper(),
            entry_conditions=[Condition(**c)
                              for c in payload.get("entry_conditions", [])],
            entry_logic=payload.get("entry_logic", "AND").upper(),
            exit_conditions=[Condition(**c)
                             for c in payload.get("exit_conditions", [])],
            exit_logic=payload.get("exit_logic", "OR").upper(),
            stop_loss_pct=payload.get("stop_loss_pct"),
            target_pct=payload.get("target_pct"),
            trailing_stop_pct=payload.get("trailing_stop_pct"),
            atr_stop_multiple=payload.get("atr_stop_multiple"),
            max_holding_bars=payload.get("max_holding_bars", 40),
            position_size_pct=payload.get("position_size_pct", 100.0),
            slippage_pct=payload.get("slippage_pct", 0.05),
            segment=payload.get("segment", "EQUITY_DELIVERY"),
            allow_pyramiding=payload.get("allow_pyramiding", False),
        )

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["entry_conditions"] = [asdict(c) for c in self.entry_conditions]
        payload["exit_conditions"] = [asdict(c) for c in self.exit_conditions]
        return payload


@dataclass
class Trade:
    symbol: str
    direction: str
    entry_date: date
    entry_price: float
    exit_date: Optional[date] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    quantity: float = 0.0
    gross_pnl: Optional[float] = None
    costs: Optional[float] = None
    net_pnl: Optional[float] = None
    return_pct: Optional[float] = None
    holding_bars: Optional[int] = None
    max_favourable_pct: Optional[float] = None
    max_adverse_pct: Optional[float] = None
    sample: str = "IS"

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["entry_date"] = self.entry_date.isoformat()
        payload["exit_date"] = self.exit_date.isoformat() if self.exit_date else None
        return payload


# --------------------------------------------------------------------------
# Engine
# --------------------------------------------------------------------------


class BacktestService:

    def run(
        self,
        spec: StrategySpec,
        data: Dict[str, pd.DataFrame],
        initial_capital: float = 100_000.0,
        in_sample_end: Optional[date] = None,
        brokerage: Optional[BrokerageModel] = None,
        walk_forward_folds: int = 0,
    ) -> Dict[str, Any]:
        """`data` maps symbol -> OHLCV frame with a DatetimeIndex."""
        warnings: List[str] = []
        all_trades: List[Trade] = []
        bars_used = 0

        if not spec.entry_conditions:
            return {"error": "The strategy has no entry condition.",
                    "methodology": METHODOLOGY}

        for symbol, frame in data.items():
            if frame is None or len(frame) < 60:
                warnings.append(
                    f"{symbol}: skipped - {0 if frame is None else len(frame)} "
                    f"bars is below the 60-bar minimum."
                )
                continue
            enriched = ind.compute_all(frame)
            bars_used += len(enriched)
            try:
                trades = self._simulate(spec, symbol, enriched, initial_capital,
                                        brokerage)
            except ValueError as exc:
                warnings.append(f"{symbol}: {exc}")
                continue
            all_trades.extend(trades)

        if not all_trades:
            return {
                "trades": [], "metrics": _empty_metrics(),
                "warnings": warnings + [
                    "No trades were generated. Either the conditions never fired "
                    "or every candidate was filtered out."
                ],
                "assumptions": self._assumptions(spec, initial_capital, brokerage),
                "bars_used": bars_used,
                "methodology": METHODOLOGY,
            }

        all_trades.sort(key=lambda t: t.entry_date)

        # Tag samples
        if in_sample_end:
            for trade in all_trades:
                trade.sample = "IS" if trade.entry_date <= in_sample_end else "OOS"

        equity_curve = self._equity_curve(all_trades, initial_capital)
        overall = self._metrics(all_trades, equity_curve, initial_capital)

        in_sample = [t for t in all_trades if t.sample == "IS"]
        out_sample = [t for t in all_trades if t.sample == "OOS"]

        result: Dict[str, Any] = {
            "strategy": spec.to_dict(),
            "trades": [t.to_dict() for t in all_trades],
            "metrics": overall,
            "equity_curve": equity_curve,
            "warnings": warnings,
            "assumptions": self._assumptions(spec, initial_capital, brokerage),
            "bars_used": bars_used,
            "methodology": METHODOLOGY,
        }

        if in_sample_end and in_sample and out_sample:
            result["in_sample_metrics"] = self._metrics(
                in_sample, self._equity_curve(in_sample, initial_capital),
                initial_capital,
            )
            result["out_of_sample_metrics"] = self._metrics(
                out_sample, self._equity_curve(out_sample, initial_capital),
                initial_capital,
            )
            result["degradation_note"] = self._degradation(
                result["in_sample_metrics"], result["out_of_sample_metrics"]
            )
        elif in_sample_end:
            warnings.append(
                "An in-sample split was requested but one side had no trades, "
                "so no out-of-sample comparison is available."
            )

        if walk_forward_folds >= 2:
            result["walk_forward"] = self._walk_forward(
                all_trades, initial_capital, walk_forward_folds
            )

        return result

    # ------------------------------------------------------------------

    def _simulate(self, spec: StrategySpec, symbol: str, frame: pd.DataFrame,
                  capital: float, brokerage: Optional[BrokerageModel]) -> List[Trade]:
        entry_signal = self._combine(
            [c.evaluate(frame) for c in spec.entry_conditions], spec.entry_logic
        )
        exit_signal = (
            self._combine([c.evaluate(frame) for c in spec.exit_conditions],
                          spec.exit_logic)
            if spec.exit_conditions else pd.Series(False, index=frame.index)
        )

        is_long = spec.direction == "LONG"
        slippage = spec.slippage_pct / 100.0

        opens = frame["open"].to_numpy(dtype="float64")
        highs = frame["high"].to_numpy(dtype="float64")
        lows = frame["low"].to_numpy(dtype="float64")
        closes = frame["close"].to_numpy(dtype="float64")
        atrs = frame["atr_14"].to_numpy(dtype="float64") if "atr_14" in frame else None
        dates = [ts.date() for ts in frame.index]

        entries = entry_signal.fillna(False).to_numpy()
        exits = exit_signal.fillna(False).to_numpy()

        trades: List[Trade] = []
        position: Optional[Dict[str, Any]] = None

        for i in range(len(frame) - 1):
            if position is None:
                if not entries[i]:
                    continue
                # ---- fill at the NEXT bar's open: the signal bar's close was
                # not knowable when the signal formed.
                fill_index = i + 1
                raw_fill = opens[fill_index]
                if not np.isfinite(raw_fill) or raw_fill <= 0:
                    continue
                fill = raw_fill * (1 + slippage) if is_long else raw_fill * (1 - slippage)

                stop = target = None
                if spec.atr_stop_multiple and atrs is not None \
                        and np.isfinite(atrs[i]):
                    offset = spec.atr_stop_multiple * atrs[i]
                    stop = fill - offset if is_long else fill + offset
                elif spec.stop_loss_pct:
                    factor = spec.stop_loss_pct / 100.0
                    stop = fill * (1 - factor) if is_long else fill * (1 + factor)
                if spec.target_pct:
                    factor = spec.target_pct / 100.0
                    target = fill * (1 + factor) if is_long else fill * (1 - factor)

                quantity = (capital * spec.position_size_pct / 100.0) / fill
                position = {
                    "entry_index": fill_index, "entry_price": fill,
                    "entry_date": dates[fill_index], "stop": stop,
                    "target": target, "quantity": quantity,
                    "best": fill, "worst": fill,
                    "trail_anchor": fill,
                }
                continue

            # ---- manage an open position on bar i ------------------------
            if i <= position["entry_index"]:
                continue

            high, low, close = highs[i], lows[i], closes[i]
            position["best"] = max(position["best"], high) if is_long \
                else min(position["best"], low)
            position["worst"] = min(position["worst"], low) if is_long \
                else max(position["worst"], high)

            if spec.trailing_stop_pct:
                factor = spec.trailing_stop_pct / 100.0
                if is_long:
                    position["trail_anchor"] = max(position["trail_anchor"], high)
                    trail = position["trail_anchor"] * (1 - factor)
                    position["stop"] = max(position["stop"] or trail, trail)
                else:
                    position["trail_anchor"] = min(position["trail_anchor"], low)
                    trail = position["trail_anchor"] * (1 + factor)
                    position["stop"] = min(position["stop"] or trail, trail)

            exit_price = exit_reason = None
            stop, target = position["stop"], position["target"]

            stop_hit = stop is not None and (low <= stop if is_long else high >= stop)
            target_hit = (
                target is not None and (high >= target if is_long else low <= target)
            )

            if stop_hit and target_hit:
                # Both touched inside one bar. Take the stop - the pessimistic
                # reading. Anything else silently inflates the result.
                exit_price, exit_reason = stop, "STOP_LOSS (ambiguous bar)"
            elif stop_hit:
                exit_price, exit_reason = stop, "STOP_LOSS"
            elif target_hit:
                exit_price, exit_reason = target, "TARGET"
            elif exits[i]:
                # Rule-based exit fills at the next open, same as entry.
                if i + 1 < len(frame):
                    exit_price, exit_reason = opens[i + 1], "EXIT_RULE"
            elif spec.max_holding_bars and \
                    (i - position["entry_index"]) >= spec.max_holding_bars:
                if i + 1 < len(frame):
                    exit_price, exit_reason = opens[i + 1], "MAX_HOLDING"

            if exit_price is None or not np.isfinite(exit_price):
                continue

            filled_exit = (
                exit_price * (1 - slippage) if is_long else exit_price * (1 + slippage)
            )
            trades.append(self._close(
                spec, symbol, position, filled_exit, exit_reason,
                dates[min(i + 1, len(dates) - 1)], i, is_long, brokerage,
            ))
            position = None

        # Force-close anything still open at the end of the series, and label
        # it, so the metrics are not quietly missing an open loser.
        if position is not None:
            last = len(frame) - 1
            filled_exit = closes[last] * (1 - slippage if is_long else 1 + slippage)
            trades.append(self._close(
                spec, symbol, position, filled_exit, "END_OF_DATA",
                dates[last], last, is_long, brokerage,
            ))

        return trades

    @staticmethod
    def _close(spec, symbol, position, exit_price, reason, exit_date,
               exit_index, is_long, brokerage) -> Trade:
        quantity = position["quantity"]
        entry = position["entry_price"]
        gross = (exit_price - entry) * quantity
        if not is_long:
            gross = -gross

        buy_value = quantity * (entry if is_long else exit_price)
        sell_value = quantity * (exit_price if is_long else entry)
        costs = estimate_charges(buy_value, sell_value, spec.segment,
                                 brokerage)["total"]
        net = gross - costs

        return Trade(
            symbol=symbol,
            direction=spec.direction,
            entry_date=position["entry_date"],
            entry_price=round(entry, 4),
            exit_date=exit_date,
            exit_price=round(exit_price, 4),
            exit_reason=reason,
            quantity=round(quantity, 4),
            gross_pnl=round(gross, 2),
            costs=round(costs, 2),
            net_pnl=round(net, 2),
            return_pct=round(net / (entry * quantity) * 100.0, 3)
            if entry and quantity else None,
            holding_bars=exit_index - position["entry_index"],
            max_favourable_pct=round(
                (position["best"] / entry - 1.0) * 100.0 * (1 if is_long else -1), 2
            ),
            max_adverse_pct=round(
                (position["worst"] / entry - 1.0) * 100.0 * (1 if is_long else -1), 2
            ),
        )

    @staticmethod
    def _combine(series_list: List[pd.Series], logic: str) -> pd.Series:
        if not series_list:
            raise ValueError("no conditions supplied")
        combined = series_list[0].fillna(False)
        for series in series_list[1:]:
            filled = series.fillna(False)
            combined = (combined & filled) if logic == "AND" else (combined | filled)
        return combined

    # ------------------------------------------------------------------
    # metrics
    # ------------------------------------------------------------------

    @staticmethod
    def _equity_curve(trades: List[Trade],
                      initial_capital: float) -> List[Dict[str, Any]]:
        equity = initial_capital
        curve = []
        for trade in trades:
            equity += trade.net_pnl or 0.0
            curve.append({
                "date": (trade.exit_date or trade.entry_date).isoformat(),
                "equity": round(equity, 2),
                "trade_pnl": trade.net_pnl,
                "symbol": trade.symbol,
            })
        return curve

    def _metrics(self, trades: List[Trade], curve: List[Dict[str, Any]],
                 initial_capital: float) -> Dict[str, Any]:
        if not trades:
            return _empty_metrics()

        returns = np.array([t.return_pct or 0.0 for t in trades]) / 100.0
        pnls = np.array([t.net_pnl or 0.0 for t in trades])
        wins = pnls[pnls > 0]
        losses = pnls[pnls < 0]

        equity = np.array([initial_capital] + [p["equity"] for p in curve])
        running_max = np.maximum.accumulate(equity)
        drawdowns = (equity - running_max) / running_max * 100.0
        max_drawdown = float(np.min(drawdowns)) if len(drawdowns) else 0.0

        start = min(t.entry_date for t in trades)
        end = max((t.exit_date or t.entry_date) for t in trades)
        years = max((end - start).days / 365.25, 1e-9)

        final_equity = float(equity[-1])
        cagr = (
            ((final_equity / initial_capital) ** (1 / years) - 1) * 100.0
            if final_equity > 0 and years > 0 else None
        )

        # Sharpe/Sortino on per-trade returns, annualised by trade frequency.
        # Not the same as a daily-return Sharpe; the payload says so.
        trades_per_year = len(trades) / years
        mean_r = float(np.mean(returns))
        std_r = float(np.std(returns, ddof=1)) if len(returns) > 1 else 0.0
        downside = returns[returns < 0]
        downside_std = (
            float(np.std(downside, ddof=1)) if len(downside) > 1 else 0.0
        )

        sharpe = (
            mean_r / std_r * math.sqrt(trades_per_year)
            if std_r > 0 else None
        )
        sortino = (
            mean_r / downside_std * math.sqrt(trades_per_year)
            if downside_std > 0 else None
        )

        gross_profit = float(wins.sum()) if len(wins) else 0.0
        gross_loss = float(abs(losses.sum())) if len(losses) else 0.0
        win_rate = len(wins) / len(trades) * 100.0
        avg_win = float(np.mean(wins)) if len(wins) else 0.0
        avg_loss = float(np.mean(losses)) if len(losses) else 0.0

        expectancy = (
            (win_rate / 100.0) * avg_win + (1 - win_rate / 100.0) * avg_loss
        )

        streaks = self._streaks(pnls)
        exposure = sum(t.holding_bars or 0 for t in trades)

        return {
            "total_trades": len(trades),
            "winning_trades": int(len(wins)),
            "losing_trades": int(len(losses)),
            "win_rate_pct": round(win_rate, 2),
            "loss_rate_pct": round(100.0 - win_rate, 2),
            "gross_profit": round(gross_profit, 2),
            "gross_loss": round(gross_loss, 2),
            "net_pnl": round(float(pnls.sum()), 2),
            "total_costs": round(sum(t.costs or 0.0 for t in trades), 2),
            "final_equity": round(final_equity, 2),
            "total_return_pct": round(
                (final_equity / initial_capital - 1) * 100.0, 2
            ),
            "cagr_pct": round(cagr, 2) if cagr is not None else None,
            "sharpe_ratio": round(sharpe, 2) if sharpe is not None else None,
            "sortino_ratio": round(sortino, 2) if sortino is not None else None,
            "max_drawdown_pct": round(max_drawdown, 2),
            "profit_factor": (
                round(gross_profit / gross_loss, 2) if gross_loss > 0
                else None
            ),
            "average_win": round(avg_win, 2),
            "average_loss": round(avg_loss, 2),
            "expectancy_per_trade": round(expectancy, 2),
            "expectancy_r": (
                round(expectancy / abs(avg_loss), 2) if avg_loss else None
            ),
            "average_holding_bars": round(
                float(np.mean([t.holding_bars or 0 for t in trades])), 1
            ),
            "total_bars_in_market": exposure,
            "max_consecutive_wins": streaks["max_wins"],
            "max_consecutive_losses": streaks["max_losses"],
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "years": round(years, 2),
            "exit_reasons": _count_reasons(trades),
            "notes": [
                "Sharpe and Sortino are computed on per-trade returns and "
                "annualised by trade frequency. They are not comparable with a "
                "daily-return Sharpe from another tool.",
                "Profit factor is undefined when there are no losing trades - "
                "it is reported as null rather than infinity.",
                "Max drawdown is measured on the closed-trade equity curve, so "
                "it understates intra-trade drawdown.",
            ],
        }

    @staticmethod
    def _streaks(pnls: np.ndarray) -> Dict[str, int]:
        max_wins = max_losses = current_wins = current_losses = 0
        for value in pnls:
            if value > 0:
                current_wins += 1
                current_losses = 0
            elif value < 0:
                current_losses += 1
                current_wins = 0
            else:
                current_wins = current_losses = 0
            max_wins = max(max_wins, current_wins)
            max_losses = max(max_losses, current_losses)
        return {"max_wins": max_wins, "max_losses": max_losses}

    @staticmethod
    def _degradation(in_sample: Dict[str, Any],
                     out_sample: Dict[str, Any]) -> str:
        parts = []
        for key, label in (("win_rate_pct", "win rate"),
                           ("profit_factor", "profit factor"),
                           ("expectancy_per_trade", "expectancy")):
            a, b = in_sample.get(key), out_sample.get(key)
            if a is None or b is None or a == 0:
                continue
            change = (b - a) / abs(a) * 100.0
            parts.append(f"{label} moved {change:+.0f}% out of sample")
        if not parts:
            return "Not enough comparable metrics to judge degradation."
        return (
            "Out-of-sample comparison: " + "; ".join(parts) +
            ". A large fall is the normal signature of a strategy fitted to the "
            "in-sample period."
        )

    @staticmethod
    def _walk_forward(trades: List[Trade], initial_capital: float,
                      folds: int) -> Dict[str, Any]:
        """Split chronologically into equal folds and report each separately."""
        if len(trades) < folds * 3:
            return {
                "folds": [],
                "note": f"Only {len(trades)} trades - too few to split into "
                        f"{folds} meaningful folds.",
            }
        service = BacktestService()
        chunk = len(trades) // folds
        results = []
        for f in range(folds):
            start = f * chunk
            end = (f + 1) * chunk if f < folds - 1 else len(trades)
            subset = trades[start:end]
            metrics = service._metrics(
                subset, service._equity_curve(subset, initial_capital),
                initial_capital,
            )
            results.append({
                "fold": f + 1,
                "period_start": metrics["period_start"],
                "period_end": metrics["period_end"],
                "trades": metrics["total_trades"],
                "win_rate_pct": metrics["win_rate_pct"],
                "net_pnl": metrics["net_pnl"],
                "profit_factor": metrics["profit_factor"],
                "max_drawdown_pct": metrics["max_drawdown_pct"],
            })
        profitable = sum(1 for r in results if (r["net_pnl"] or 0) > 0)
        return {
            "folds": results,
            "profitable_folds": profitable,
            "total_folds": folds,
            "consistency_note": (
                f"{profitable} of {folds} folds were profitable. A strategy that "
                f"only works in one fold is describing that period, not an edge."
            ),
        }

    @staticmethod
    def _assumptions(spec: StrategySpec, capital: float,
                     brokerage: Optional[BrokerageModel]) -> Dict[str, Any]:
        model = brokerage or BrokerageModel()
        return {
            "initial_capital": capital,
            "position_size_pct": spec.position_size_pct,
            "slippage_pct_per_leg": spec.slippage_pct,
            "segment": spec.segment,
            "charges_model": {
                "brokerage_per_order": model.brokerage_per_order,
                "brokerage_pct": model.brokerage_pct,
                "gst_pct": model.gst_pct,
                "note": model.source_note,
            },
            "fill_rule": (
                "Entries and rule-based exits fill at the NEXT bar's open. "
                "Stop and target fills are assumed at the level itself."
            ),
            "intrabar_rule": (
                "When one bar's range touches both the stop and the target, the "
                "stop is taken. Tick data would be needed to resolve the order."
            ),
            "gap_rule": (
                "A gap through a stop fills at the stop price in this model. In "
                "reality it fills at the gap, so real losses can exceed these."
            ),
            "survivorship": (
                "The universe tested is whatever was supplied. If it contains "
                "only currently listed names, results carry survivorship bias."
            ),
            "corporate_actions": (
                "Bars come from the history provider with adjusted closes. "
                "Unadjusted splits or bonuses would show as false gaps."
            ),
            "no_lookahead": (
                "All indicators are causal and every fill happens strictly "
                "after the bar that generated the signal."
            ),
        }


def _resolve(frame: pd.DataFrame, token: str | float) -> pd.Series:
    if isinstance(token, (int, float)):
        return pd.Series(float(token), index=frame.index)
    if token in frame.columns:
        return frame[token]
    try:
        return pd.Series(float(token), index=frame.index)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"'{token}' is neither a numeric literal nor an available column. "
            f"Available: {', '.join(sorted(frame.columns)[:20])}..."
        ) from exc


def _count_reasons(trades: List[Trade]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for trade in trades:
        key = trade.exit_reason or "UNKNOWN"
        out[key] = out.get(key, 0) + 1
    return out


def _empty_metrics() -> Dict[str, Any]:
    return {
        "total_trades": 0, "winning_trades": 0, "losing_trades": 0,
        "win_rate_pct": None, "net_pnl": 0.0, "cagr_pct": None,
        "sharpe_ratio": None, "sortino_ratio": None, "max_drawdown_pct": None,
        "profit_factor": None, "expectancy_per_trade": None,
        "note": "No trades were generated, so no metric can be computed.",
    }


backtest_service = BacktestService()
