"""Black-Scholes-Merton pricing, Greeks and the implied-volatility solver.

The important properties here are the ones that stop the model lying:
put-call parity holds, Greeks carry the right signs, the IV solver refuses
premiums outside the no-arbitrage bounds, and an expired contract reports
non-convergence instead of returning a number.
"""

import math
from datetime import date, datetime, timedelta, timezone

import pytest

from app.services import greeks as gk


TODAY = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
EXPIRY = date(2026, 2, 26)


def test_put_call_parity_holds():
    """C - P = S*e^(-qT) - K*e^(-rT)."""
    S, K, r, q, sigma = 1000.0, 1000.0, 0.065, 0.01, 0.25
    T = gk.time_to_expiry_years(EXPIRY, now=TODAY)

    call = gk.bsm_price(S, K, T, r, q, sigma, "CE")
    put = gk.bsm_price(S, K, T, r, q, sigma, "PE")
    expected = S * math.exp(-q * T) - K * math.exp(-r * T)

    assert (call - put) == pytest.approx(expected, abs=1e-8)


def test_price_is_monotonic_in_volatility():
    T = gk.time_to_expiry_years(EXPIRY, now=TODAY)
    low = gk.bsm_price(1000, 1000, T, 0.065, 0.0, 0.15, "CE")
    high = gk.bsm_price(1000, 1000, T, 0.065, 0.0, 0.35, "CE")
    assert high > low


def test_at_expiry_price_collapses_to_intrinsic_value():
    assert gk.bsm_price(1100, 1000, 0.0, 0.065, 0.0, 0.25, "CE") == 100.0
    assert gk.bsm_price(900, 1000, 0.0, 0.065, 0.0, 0.25, "PE") == 100.0
    assert gk.bsm_price(900, 1000, 0.0, 0.065, 0.0, 0.25, "CE") == 0.0


# --------------------------------------------------------------------------
# Greek signs and magnitudes
# --------------------------------------------------------------------------


def test_call_delta_is_positive_and_below_one():
    result = gk.compute_greeks(1000, 1000, EXPIRY, "CE", volatility=0.25,
                               now=TODAY)
    assert 0 < result.delta < 1


def test_put_delta_is_negative_and_above_minus_one():
    result = gk.compute_greeks(1000, 1000, EXPIRY, "PE", volatility=0.25,
                               now=TODAY)
    assert -1 < result.delta < 0


def test_atm_delta_is_near_half():
    result = gk.compute_greeks(1000, 1000, EXPIRY, "CE", volatility=0.25,
                               now=TODAY)
    assert result.delta == pytest.approx(0.5, abs=0.12)


def test_gamma_and_vega_are_positive_for_both_sides():
    for option_type in ("CE", "PE"):
        result = gk.compute_greeks(1000, 1000, EXPIRY, option_type,
                                   volatility=0.25, now=TODAY)
        assert result.gamma > 0
        assert result.vega > 0


def test_theta_is_negative_for_a_bought_option():
    result = gk.compute_greeks(1000, 1000, EXPIRY, "CE", volatility=0.25,
                               now=TODAY)
    assert result.theta < 0


def test_gamma_is_highest_at_the_money():
    atm = gk.compute_greeks(1000, 1000, EXPIRY, "CE", volatility=0.25, now=TODAY)
    otm = gk.compute_greeks(1000, 1300, EXPIRY, "CE", volatility=0.25, now=TODAY)
    assert atm.gamma > otm.gamma


def test_gamma_rises_as_expiry_approaches():
    far = gk.compute_greeks(1000, 1000, date(2026, 6, 25), "CE",
                            volatility=0.25, now=TODAY)
    near = gk.compute_greeks(1000, 1000, date(2026, 1, 8), "CE",
                             volatility=0.25, now=TODAY)
    assert near.gamma > far.gamma


def test_moneyness_classification():
    assert gk.classify_moneyness(1000, 900, "CE") == "ITM"
    assert gk.classify_moneyness(1000, 1100, "CE") == "OTM"
    assert gk.classify_moneyness(1000, 1100, "PE") == "ITM"
    assert gk.classify_moneyness(1000, 1000, "PE") == "ATM"


# --------------------------------------------------------------------------
# Implied volatility
# --------------------------------------------------------------------------


def test_implied_volatility_recovers_the_input_volatility():
    T = gk.time_to_expiry_years(EXPIRY, now=TODAY)
    target_sigma = 0.283
    premium = gk.bsm_price(1000, 1050, T, 0.065, 0.0, target_sigma, "CE")

    sigma, converged, reason = gk.implied_volatility(
        premium, 1000, 1050, T, 0.065, 0.0, "CE"
    )
    assert converged, reason
    assert sigma == pytest.approx(target_sigma, abs=1e-4)


def test_implied_volatility_refuses_a_premium_below_the_arbitrage_floor():
    T = gk.time_to_expiry_years(EXPIRY, now=TODAY)
    sigma, converged, reason = gk.implied_volatility(
        0.01, 1200, 1000, T, 0.065, 0.0, "CE"
    )
    assert sigma is None
    assert not converged
    assert "no-arbitrage floor" in reason


def test_implied_volatility_refuses_a_premium_above_the_ceiling():
    T = gk.time_to_expiry_years(EXPIRY, now=TODAY)
    sigma, converged, reason = gk.implied_volatility(
        5000.0, 1000, 1000, T, 0.065, 0.0, "CE"
    )
    assert sigma is None
    assert "ceiling" in reason


def test_zero_premium_is_rejected_rather_than_solved():
    T = gk.time_to_expiry_years(EXPIRY, now=TODAY)
    sigma, converged, reason = gk.implied_volatility(
        0.0, 1000, 1000, T, 0.065, 0.0, "CE"
    )
    assert sigma is None
    assert "zero or missing" in reason


# --------------------------------------------------------------------------
# Degenerate inputs
# --------------------------------------------------------------------------


def test_expired_contract_reports_non_convergence_not_a_number():
    result = gk.compute_greeks(1000, 1000, date(2025, 1, 1), "CE",
                               market_price=25.0, now=TODAY)
    assert result.converged is False
    assert "expired" in result.failure_reason
    assert result.delta is None


def test_missing_spot_is_reported_not_guessed():
    result = gk.compute_greeks(None, 1000, EXPIRY, "CE", market_price=25.0,
                               now=TODAY)
    assert result.converged is False
    assert "underlying price unavailable" in result.failure_reason


def test_assumptions_are_always_returned():
    result = gk.compute_greeks(1000, 1000, EXPIRY, "CE", volatility=0.25,
                               now=TODAY)
    payload = result.to_dict()
    assumptions = payload["assumptions"]
    assert assumptions["model"] == "black_scholes_merton"
    assert assumptions["risk_free_rate"] == gk.DEFAULT_RISK_FREE_RATE
    assert "European exercise" in assumptions["notes"]


def test_every_greek_has_a_plain_language_explanation():
    result = gk.compute_greeks(1000, 1000, EXPIRY, "CE", volatility=0.25,
                               now=TODAY)
    for greek in ("delta", "gamma", "theta", "vega", "rho"):
        assert greek in result.explanations
        assert len(result.explanations[greek]) > 40


def test_delta_explanation_does_not_claim_to_be_a_probability():
    result = gk.compute_greeks(1000, 1000, EXPIRY, "CE", volatility=0.25,
                               now=TODAY)
    assert "not a probability" in result.explanations["delta"]


# --------------------------------------------------------------------------
# Time to expiry
# --------------------------------------------------------------------------


def test_time_to_expiry_is_zero_after_expiry():
    assert gk.time_to_expiry_years(date(2025, 1, 1), now=TODAY) == 0.0


def test_trading_day_basis_differs_from_calendar_basis():
    calendar = gk.time_to_expiry_years(EXPIRY, now=TODAY, basis="calendar")
    trading = gk.time_to_expiry_years(EXPIRY, now=TODAY, basis="trading")
    assert calendar != trading
    assert trading > 0
