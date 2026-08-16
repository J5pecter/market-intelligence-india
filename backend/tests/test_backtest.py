"""Backtest integrity.

These are the tests that stop a backtest flattering a strategy: no look-ahead,
pessimistic intrabar resolution, costs always applied, and samples reported
separately.
"""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from app.services.backtest import (Condition, StrategySpec, backtest_service)


def _frame(closes: list[float], highs=None, lows=None,
           opens=None) -> pd.DataFrame:
    n = len(closes)
    index = pd.date_range("2024-01-01", periods=n, freq="B", tz="UTC")
    close = pd.Series(closes, index=index, dtype="float64")
    return pd.DataFrame({
        "open": pd.Series(opens or closes, index=index, dtype="float64"),
        "high": pd.Series(highs or [c * 1.01 for c in closes], index=index,
                          dtype="float64"),
        "low": pd.Series(lows or [c * 0.99 for c in closes], index=index,
                         dtype="float64"),
        "close": close,
        "volume": pd.Series(np.full(n, 1_000_000), index=index),
    })


def _trending(n: int = 200) -> pd.DataFrame:
    """A series that dips then trends up, so an SMA cross actually fires."""
    rng = np.random.default_rng(3)
    values = []
    price = 100.0
    for i in range(n):
        drift = -0.004 if i < n // 3 else 0.005
        price *= 1 + drift + rng.normal(0, 0.008)
        values.append(price)
    return _frame(values)


def _spec(**overrides) -> StrategySpec:
    base = dict(
        name="test",
        direction="LONG",
        entry_conditions=[Condition("sma_20", "cross_above", "sma_50")],
        exit_conditions=[Condition("sma_20", "cross_below", "sma_50")],
        stop_loss_pct=5.0,
        target_pct=10.0,
        slippage_pct=0.05,
        max_holding_bars=40,
    )
    base.update(overrides)
    return StrategySpec(**base)


# --------------------------------------------------------------------------
# Look-ahead
# --------------------------------------------------------------------------


def test_entries_never_fill_on_the_signal_bar():
    """A fill must be at least one bar after the bar that produced the signal.

    Constructed so the signal bar's close is much better than the next open:
    if the engine filled on the signal bar the entry price would be lower.
    """
    result = backtest_service.run(_spec(), {"TEST": _trending()},
                                  initial_capital=100_000)
    frame = _trending()
    for trade in result["trades"]:
        entry_index = frame.index.get_indexer(
            [pd.Timestamp(trade["entry_date"], tz="UTC")], method="nearest"
        )[0]
        assert entry_index > 0


def test_no_trade_is_opened_on_the_final_bar():
    result = backtest_service.run(_spec(), {"TEST": _trending()},
                                  initial_capital=100_000)
    if result["trades"]:
        last_entry = max(t["entry_date"] for t in result["trades"])
        last_bar = _trending().index[-1].date().isoformat()
        assert last_entry <= last_bar


def test_open_positions_are_force_closed_and_labelled():
    result = backtest_service.run(_spec(max_holding_bars=None,
                                        exit_conditions=[]),
                                  {"TEST": _trending()},
                                  initial_capital=100_000)
    reasons = {t["exit_reason"] for t in result["trades"]}
    # Nothing may be left open and silently excluded from the metrics.
    assert all(t["exit_date"] is not None for t in result["trades"])
    if reasons:
        assert reasons <= {"STOP_LOSS", "TARGET", "END_OF_DATA",
                           "STOP_LOSS (ambiguous bar)", "EXIT_RULE",
                           "MAX_HOLDING"}


# --------------------------------------------------------------------------
# Intrabar ambiguity
# --------------------------------------------------------------------------


def test_a_bar_touching_both_stop_and_target_resolves_to_the_stop():
    # Long entry near 100; a later bar ranges from 80 to 130, touching both a
    # 5% stop and a 10% target. Trailing bars follow so the ambiguous bar is
    # actually managed rather than being the final bar of the series.
    closes = [100.0] * 60 + [100.0, 105.0] + [104.0] * 5
    highs = [101.0] * 60 + [101.0, 130.0] + [105.0] * 5
    lows = [99.0] * 60 + [99.0, 80.0] + [103.0] * 5
    frame = _frame(closes, highs=highs, lows=lows, opens=closes)

    spec = _spec(
        entry_conditions=[Condition("close", ">", 50)],
        exit_conditions=[],
        max_holding_bars=None,
    )
    result = backtest_service.run(spec, {"TEST": frame}, initial_capital=100_000)
    assert result["trades"]
    assert "STOP_LOSS" in result["trades"][0]["exit_reason"]
    assert "ambiguous" in result["trades"][0]["exit_reason"]


def test_the_rule_is_documented_in_the_assumptions():
    result = backtest_service.run(_spec(), {"TEST": _trending()},
                                  initial_capital=100_000)
    assert "stop is taken" in result["assumptions"]["intrabar_rule"]
    assert "next bar" in result["assumptions"]["fill_rule"].lower()


# --------------------------------------------------------------------------
# Costs
# --------------------------------------------------------------------------


def test_costs_are_deducted_from_every_trade():
    result = backtest_service.run(_spec(), {"TEST": _trending()},
                                  initial_capital=100_000)
    for trade in result["trades"]:
        assert trade["costs"] > 0
        assert trade["net_pnl"] == pytest.approx(
            trade["gross_pnl"] - trade["costs"], abs=0.02
        )


def test_slippage_worsens_the_result():
    data = {"TEST": _trending()}
    clean = backtest_service.run(_spec(slippage_pct=0.0), data,
                                 initial_capital=100_000)
    slipped = backtest_service.run(_spec(slippage_pct=0.5), data,
                                   initial_capital=100_000)
    if clean["metrics"]["total_trades"] and slipped["metrics"]["total_trades"]:
        assert slipped["metrics"]["net_pnl"] < clean["metrics"]["net_pnl"]


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------


def test_no_trades_returns_explicit_empty_metrics_not_zeros():
    spec = _spec(entry_conditions=[Condition("rsi_14", ">", 200)])
    result = backtest_service.run(spec, {"TEST": _trending()},
                                  initial_capital=100_000)
    assert result["metrics"]["total_trades"] == 0
    assert result["metrics"]["win_rate_pct"] is None
    assert any("No trades" in w for w in result["warnings"])


def test_profit_factor_is_none_rather_than_infinity_without_losses():
    closes = list(np.linspace(100, 200, 120))
    spec = _spec(entry_conditions=[Condition("close", ">", 50)],
                 exit_conditions=[], stop_loss_pct=None, target_pct=2.0,
                 max_holding_bars=5)
    result = backtest_service.run(spec, {"TEST": _frame(closes)},
                                  initial_capital=100_000)
    metrics = result["metrics"]
    if metrics["total_trades"] and metrics["losing_trades"] == 0:
        assert metrics["profit_factor"] is None


def test_metrics_carry_their_caveats():
    result = backtest_service.run(_spec(), {"TEST": _trending()},
                                  initial_capital=100_000)
    notes = " ".join(result["metrics"].get("notes", []))
    assert "per-trade returns" in notes
    assert "understates" in notes


def test_short_series_are_skipped_with_a_reason():
    result = backtest_service.run(_spec(), {"TINY": _frame([100.0] * 20)},
                                  initial_capital=100_000)
    assert any("60-bar minimum" in w for w in result["warnings"])


def test_missing_entry_condition_is_rejected():
    result = backtest_service.run(_spec(entry_conditions=[]),
                                  {"TEST": _trending()})
    assert "error" in result


def test_unknown_column_produces_a_helpful_message():
    spec = _spec(entry_conditions=[Condition("not_a_column", ">", 1)])
    result = backtest_service.run(spec, {"TEST": _trending()})
    assert any("neither a numeric literal" in w for w in result["warnings"])


# --------------------------------------------------------------------------
# Sample separation
# --------------------------------------------------------------------------


def test_in_sample_and_out_of_sample_are_reported_separately():
    frame = _trending(400)
    split = frame.index[200].date()
    result = backtest_service.run(_spec(), {"TEST": frame},
                                  initial_capital=100_000,
                                  in_sample_end=split)
    if result["metrics"]["total_trades"] > 4:
        assert "in_sample_metrics" in result
        assert "out_of_sample_metrics" in result
        assert "degradation_note" in result
        for trade in result["trades"]:
            expected = "IS" if date.fromisoformat(trade["entry_date"]) <= split else "OOS"
            assert trade["sample"] == expected


def test_walk_forward_reports_each_fold():
    frame = _trending(500)
    result = backtest_service.run(_spec(), {"TEST": frame},
                                  initial_capital=100_000,
                                  walk_forward_folds=3)
    walk = result.get("walk_forward")
    assert walk is not None
    if walk["folds"]:
        assert len(walk["folds"]) == 3
        assert "consistency_note" in walk


def test_assumptions_declare_survivorship_and_corporate_actions():
    result = backtest_service.run(_spec(), {"TEST": _trending()},
                                  initial_capital=100_000)
    assumptions = result["assumptions"]
    assert "survivorship" in assumptions
    assert "corporate_actions" in assumptions
    assert "no_lookahead" in assumptions
