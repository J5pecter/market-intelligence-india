"""Trade status engine and setup arithmetic.

The specification calls out a precise list of edge cases; each has a test here:
entry exactly on the lower bound, exactly on the upper bound, price below the
range, inside it, above it, target reached, stop reached, a zero-width stop,
missing data and stale data.

The four reference cards in the specification are also asserted numerically -
if the achieved/potential formulas ever drift, these fail.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.services.trade_status import (BrokerageModel, SetupLevels, TradeStatus,
                                       estimate_charges, evaluate_status,
                                       position_size, simulate_pnl)


def levels(**kwargs) -> SetupLevels:
    base = dict(side="BUY", entry_min=100.0, entry_max=102.0, stop_loss=95.0,
                target_1=110.0)
    base.update(kwargs)
    return SetupLevels(**base)


# --------------------------------------------------------------------------
# Range membership
# --------------------------------------------------------------------------


def test_ltp_exactly_on_lower_bound_is_inside_the_range():
    result = evaluate_status(levels(), ltp=100.0)
    assert result.status is TradeStatus.WITHIN_ENTRY
    assert "inclusive" in result.reason


def test_ltp_exactly_on_upper_bound_is_inside_the_range():
    result = evaluate_status(levels(), ltp=102.0)
    assert result.status is TradeStatus.WITHIN_ENTRY


def test_ltp_below_range_is_not_activated():
    result = evaluate_status(levels(), ltp=98.0)
    assert result.status is TradeStatus.NOT_ACTIVATED


def test_ltp_above_range_without_target_is_above_entry():
    result = evaluate_status(
        levels(target_1=None), ltp=105.0
    )
    assert result.status is TradeStatus.ABOVE_ENTRY


def test_ltp_above_range_with_target_is_in_progress():
    result = evaluate_status(levels(), ltp=105.0)
    assert result.status is TradeStatus.TARGET_IN_PROGRESS


def test_short_side_inverts_the_range_logic():
    short = SetupLevels(side="SELL", entry_min=100.0, entry_max=102.0,
                        stop_loss=108.0, target_1=90.0)
    assert evaluate_status(short, ltp=101.0).status is TradeStatus.WITHIN_ENTRY
    assert evaluate_status(short, ltp=95.0).status is TradeStatus.TARGET_IN_PROGRESS
    assert evaluate_status(short, ltp=104.0).status is TradeStatus.NOT_ACTIVATED


# --------------------------------------------------------------------------
# Terminal states
# --------------------------------------------------------------------------


def test_target_reached_takes_priority_over_range_membership():
    result = evaluate_status(levels(), ltp=110.5)
    assert result.status is TradeStatus.TARGET_ACHIEVED


def test_stop_loss_reached_is_terminal_even_if_price_recovered():
    # Price is back inside the entry range, but the low went through the stop.
    result = evaluate_status(levels(), ltp=101.0, low_since_publication=94.0)
    assert result.status is TradeStatus.STOP_LOSS_TRIGGERED


def test_stop_beats_target_when_both_were_touched():
    result = evaluate_status(
        levels(), ltp=101.0,
        high_since_publication=115.0, low_since_publication=90.0,
    )
    # Stop is checked first: the pessimistic reading.
    assert result.status is TradeStatus.STOP_LOSS_TRIGGERED


def test_expired_when_validity_window_has_passed():
    past = datetime.now(tz=timezone.utc) - timedelta(days=1)
    result = evaluate_status(levels(valid_until=past), ltp=101.0)
    assert result.status is TradeStatus.EXPIRED


def test_manual_invalidation_short_circuits_everything():
    result = evaluate_status(levels(), ltp=110.5, manually_invalidated=True)
    assert result.status is TradeStatus.INVALIDATED


# --------------------------------------------------------------------------
# Missing / degraded data
# --------------------------------------------------------------------------


def test_missing_price_yields_unknown_not_a_guess():
    result = evaluate_status(levels(), ltp=None)
    assert result.status is TradeStatus.UNKNOWN
    assert result.achieved_pct is None
    assert any("No current price" in w for w in result.warnings)


def test_missing_entry_yields_unknown():
    result = evaluate_status(levels(entry_min=None, entry_max=None), ltp=100.0)
    assert result.status is TradeStatus.UNKNOWN


def test_stale_price_is_flagged_but_still_evaluated():
    result = evaluate_status(levels(), ltp=101.0, price_is_stale=True)
    assert result.status is TradeStatus.WITHIN_ENTRY
    assert any("stale" in w.lower() for w in result.warnings)


def test_missing_path_extremes_produce_an_explicit_warning():
    result = evaluate_status(levels(), ltp=101.0)
    assert any("high/low path" in w for w in result.warnings)


# --------------------------------------------------------------------------
# Risk / reward arithmetic
# --------------------------------------------------------------------------


def test_zero_width_stop_gives_undefined_risk_reward_not_infinity():
    result = evaluate_status(levels(stop_loss=102.0), ltp=101.0)
    assert result.risk_per_unit is None
    assert result.risk_reward is None
    assert any("zero" in w for w in result.warnings)


def test_risk_reward_matches_the_specification_example():
    """Entry 100, SL 95, target 110 -> risk 5, reward 10, R:R 1:2."""
    setup = SetupLevels(side="BUY", entry_min=100.0, entry_max=100.0,
                        stop_loss=95.0, target_1=110.0)
    result = evaluate_status(setup, ltp=100.0)
    assert result.risk_per_unit == 5.0
    assert result.reward_per_unit == 10.0
    assert result.risk_reward == 2.0


def test_entry_reference_is_the_conservative_end_of_the_range():
    setup = levels()
    assert setup.entry_reference == 102.0          # top of range for a long
    short = SetupLevels(side="SELL", entry_min=100.0, entry_max=102.0)
    assert short.entry_reference == 100.0          # bottom of range for a short


def test_multi_target_ladder_reports_r_multiples():
    setup = SetupLevels(side="BUY", entry_min=100.0, entry_max=100.0,
                        stop_loss=95.0, target_1=110.0, target_2=120.0,
                        target_3=140.0)
    result = evaluate_status(setup, ltp=100.0)
    assert [t["r_multiple"] for t in result.targets] == [2.0, 4.0, 8.0]
    assert result.targets[0]["return_from_entry_pct"] == 10.0


# --------------------------------------------------------------------------
# The reference cards from the specification
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,entry_min,entry_max,target,ltp,expected_achieved,expected_potential,from_ltp",
    [
        # Equity cards quote "potential expected" from the entry reference.
        ("HDFCBANK", 725.30, 727.30, 746.00, 727.00, -0.04, 2.57, False),
        ("BAJAJELEC", 361.40, 362.40, 378.00, 362.40, 0.00, 4.30, False),
        ("VOLTAS", 1532.00, 1532.00, 1920.00, 1320.50, -13.81, 25.33, False),
        # Option cards quote "potential left" from the current premium.
        ("BDL 1440 CE", 20.00, 21.00, 55.00, 23.80, 13.33, 131.09, True),
        ("SIEMENS 3900 PE", 47.50, 48.50, 82.00, 52.15, 7.53, 57.24, True),
    ],
)
def test_reference_cards_reproduce_their_published_percentages(
    name, entry_min, entry_max, target, ltp, expected_achieved,
    expected_potential, from_ltp,
):
    setup = SetupLevels(side="BUY", entry_min=entry_min, entry_max=entry_max,
                        target_1=target)
    result = evaluate_status(setup, ltp=ltp)

    assert result.achieved_pct == pytest.approx(expected_achieved, abs=0.01), name
    actual = (
        result.potential_from_ltp_pct if from_ltp
        else result.potential_from_entry_pct
    )
    assert actual == pytest.approx(expected_potential, abs=0.01), name


# --------------------------------------------------------------------------
# Position sizing
# --------------------------------------------------------------------------


def test_position_size_respects_the_rupee_risk_budget():
    result = position_size(capital=100_000, max_loss_pct=1.0, entry=100.0,
                           stop_loss=95.0)
    assert result["max_rupee_risk"] == 1000.0
    assert result["risk_per_unit"] == 5.0
    assert result["quantity"] == 200
    assert result["capital_deployed"] == 20_000.0


def test_position_size_rounds_down_to_whole_lots():
    result = position_size(capital=100_000, max_loss_pct=2.0, entry=50.0,
                           stop_loss=45.0, lot_size=100)
    # 2000 / 5 = 400 units -> exactly 4 lots
    assert result["quantity"] == 400
    assert result["lots"] == 4


def test_position_size_refuses_a_zero_width_stop():
    result = position_size(capital=100_000, max_loss_pct=1.0, entry=100.0,
                           stop_loss=100.0)
    assert result["quantity"] == 0
    assert "zero" in result["error"]


def test_position_size_flags_when_capital_is_exceeded():
    result = position_size(capital=10_000, max_loss_pct=50.0, entry=500.0,
                           stop_loss=499.0)
    assert result["exceeds_capital"] is True
    assert result["warnings"]


# --------------------------------------------------------------------------
# P&L simulation and charges
# --------------------------------------------------------------------------


def test_flat_exit_still_loses_money_because_charges_apply():
    result = simulate_pnl(capital=100_000, entry=100.0, stop_loss=95.0,
                          target=110.0, quantity=500)
    base = next(s for s in result["scenarios"] if s["scenario"] == "Base")
    assert base["gross_pnl"] == 0.0
    assert base["net_pnl"] < 0


def test_breakeven_price_sits_above_entry_for_a_long():
    result = simulate_pnl(capital=100_000, entry=100.0, stop_loss=95.0,
                          target=110.0, quantity=500)
    assert result["breakeven_price"] > 100.0


def test_long_option_max_loss_is_the_premium():
    result = simulate_pnl(capital=100_000, entry=20.0, stop_loss=None,
                          target=55.0, quantity=1, lot_size=325,
                          segment="OPTION")
    assert result["max_theoretical_loss"] == pytest.approx(20.0 * 325, abs=0.01)


def test_charges_are_itemised_and_non_zero():
    charges = estimate_charges(100_000, 110_000, "EQUITY_DELIVERY")
    assert charges["total"] > 0
    for component in ("stt", "exchange_transaction", "sebi_charges",
                      "stamp_duty", "gst"):
        assert component in charges


def test_brokerage_model_is_overridable():
    cheap = estimate_charges(100_000, 100_000, "EQUITY_INTRADAY",
                             BrokerageModel(brokerage_per_order=0.0))
    dear = estimate_charges(100_000, 100_000, "EQUITY_INTRADAY",
                            BrokerageModel(brokerage_per_order=50.0))
    assert dear["total"] > cheap["total"]
