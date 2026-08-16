"""Black-Scholes-Merton pricing, Greeks and implied volatility.

Every number this module produces is reproducible from the assumptions it
returns alongside it: model, risk-free rate, dividend yield, time to expiry and
the volatility source. Nothing is hidden.

Known limitations, stated up front because the UI repeats them:
* Indian index and stock options are European-style on NSE, so BSM is the right
  family. American-style early exercise is not modelled.
* The risk-free rate default is a *configured assumption*, not a live curve.
* Time to expiry uses calendar time by default (the convention BSM assumes).
  A trading-day variant is provided for comparison and is clearly labelled.
* Deep OTM options with near-zero premium make implied volatility numerically
  unstable; the solver reports non-convergence rather than returning a guess.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Dict, Optional

from scipy.stats import norm

# Configured assumptions. Override per call when you have better inputs.
DEFAULT_RISK_FREE_RATE = 0.065   # ~6.5% - a placeholder for the Indian curve
DEFAULT_DIVIDEND_YIELD = 0.0
TRADING_DAYS_PER_YEAR = 252
CALENDAR_DAYS_PER_YEAR = 365.0

_MIN_T = 1.0 / (CALENDAR_DAYS_PER_YEAR * 24 * 60)  # one minute, in years
_MIN_SIGMA = 1e-6


@dataclass
class GreekAssumptions:
    model: str = "black_scholes_merton"
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE
    dividend_yield: float = DEFAULT_DIVIDEND_YIELD
    time_basis: str = "calendar/365"
    volatility_source: str = "solved from market premium"
    notes: str = (
        "European exercise assumed. The risk-free rate is a configured "
        "constant, not a live yield curve; Greeks scale with it."
    )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class GreekResult:
    price: Optional[float] = None
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None    # per calendar day
    vega: Optional[float] = None     # per 1 volatility point (1% = 0.01)
    rho: Optional[float] = None      # per 1 rate point
    implied_volatility: Optional[float] = None   # decimal, e.g. 0.284
    intrinsic_value: Optional[float] = None
    time_value: Optional[float] = None
    moneyness: Optional[str] = None
    time_to_expiry_years: Optional[float] = None
    converged: bool = True
    failure_reason: Optional[str] = None
    assumptions: GreekAssumptions = field(default_factory=GreekAssumptions)
    explanations: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["assumptions"] = self.assumptions.to_dict()
        return payload


# --------------------------------------------------------------------------
# Time to expiry
# --------------------------------------------------------------------------


def time_to_expiry_years(
    expiry: date,
    now: Optional[datetime] = None,
    basis: str = "calendar",
    expiry_time_ist: tuple[int, int] = (15, 30),
) -> float:
    """Years to expiry. Indian contracts expire at 15:30 IST on expiry day.

    `basis="trading"` counts only trading days over 252 - useful for comparing
    against how traders think about theta, but it is *not* the BSM convention,
    so it is never the default.
    """
    from app.core.market_calendar import IST

    now = (now or datetime.now(tz=timezone.utc)).astimezone(IST)
    expiry_dt = datetime(
        expiry.year, expiry.month, expiry.day,
        expiry_time_ist[0], expiry_time_ist[1], tzinfo=IST,
    )
    seconds = (expiry_dt - now).total_seconds()
    if seconds <= 0:
        return 0.0

    if basis == "trading":
        from app.core.market_calendar import trading_days_between

        days = trading_days_between(now.date(), expiry)
        # Add the fraction of today that remains.
        return max(_MIN_T, days / TRADING_DAYS_PER_YEAR)

    return max(_MIN_T, seconds / (CALENDAR_DAYS_PER_YEAR * 24 * 3600))


# --------------------------------------------------------------------------
# Pricing
# --------------------------------------------------------------------------


def _d1_d2(S: float, K: float, T: float, r: float, q: float,
           sigma: float) -> tuple[float, float]:
    vol_sqrt_t = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / vol_sqrt_t
    return d1, d1 - vol_sqrt_t


def bsm_price(S: float, K: float, T: float, r: float, q: float,
              sigma: float, option_type: str) -> float:
    """Black-Scholes-Merton price with continuous dividend yield q."""
    option_type = option_type.upper()
    if T <= 0 or sigma <= 0:
        # At expiry (or with zero vol) the option is worth its intrinsic value.
        return max(0.0, S - K) if option_type == "CE" else max(0.0, K - S)

    d1, d2 = _d1_d2(S, K, T, r, q, sigma)
    discount_r = math.exp(-r * T)
    discount_q = math.exp(-q * T)

    if option_type == "CE":
        return S * discount_q * norm.cdf(d1) - K * discount_r * norm.cdf(d2)
    return K * discount_r * norm.cdf(-d2) - S * discount_q * norm.cdf(-d1)


def implied_volatility(
    market_price: float, S: float, K: float, T: float, r: float, q: float,
    option_type: str, tolerance: float = 1e-6, max_iterations: int = 100,
) -> tuple[Optional[float], bool, Optional[str]]:
    """Solve for sigma. Brent bisection on a bracketed root - robust where
    Newton-Raphson diverges (deep OTM, near expiry).

    Returns (sigma, converged, reason_if_not).
    """
    option_type = option_type.upper()
    if market_price is None or market_price <= 0:
        return None, False, "market premium is zero or missing"
    if T <= 0:
        return None, False, "contract has expired"
    if S <= 0 or K <= 0:
        return None, False, "spot or strike is not positive"

    # No-arbitrage bounds. A premium outside them cannot be produced by any
    # volatility, so refuse rather than returning a fitted nonsense number.
    discount_r = math.exp(-r * T)
    discount_q = math.exp(-q * T)
    if option_type == "CE":
        lower_bound = max(0.0, S * discount_q - K * discount_r)
        upper_bound = S * discount_q
    else:
        lower_bound = max(0.0, K * discount_r - S * discount_q)
        upper_bound = K * discount_r

    if market_price < lower_bound - 1e-8:
        return None, False, (
            f"premium {market_price:.2f} is below the no-arbitrage floor "
            f"{lower_bound:.2f}"
        )
    if market_price > upper_bound + 1e-8:
        return None, False, (
            f"premium {market_price:.2f} exceeds the no-arbitrage ceiling "
            f"{upper_bound:.2f}"
        )

    low, high = _MIN_SIGMA, 5.0  # 0% to 500% annualised
    price_low = bsm_price(S, K, T, r, q, low, option_type)
    price_high = bsm_price(S, K, T, r, q, high, option_type)
    if not (price_low <= market_price <= price_high):
        return None, False, "premium is not bracketed within 0-500% volatility"

    for _ in range(max_iterations):
        mid = 0.5 * (low + high)
        price_mid = bsm_price(S, K, T, r, q, mid, option_type)
        if abs(price_mid - market_price) < tolerance:
            return mid, True, None
        if price_mid < market_price:
            low = mid
        else:
            high = mid
        if high - low < 1e-9:
            return mid, True, None
    return 0.5 * (low + high), False, "solver hit the iteration cap"


# --------------------------------------------------------------------------
# The full package
# --------------------------------------------------------------------------


def compute_greeks(
    spot: Optional[float],
    strike: float,
    expiry: date,
    option_type: str,
    market_price: Optional[float] = None,
    volatility: Optional[float] = None,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    dividend_yield: float = DEFAULT_DIVIDEND_YIELD,
    now: Optional[datetime] = None,
    time_basis: str = "calendar",
) -> GreekResult:
    """Greeks for one contract.

    Volatility precedence: an explicit `volatility` argument wins; otherwise we
    solve it from `market_price`. If neither is usable the result reports
    non-convergence with the reason - it never falls back to a made-up sigma.
    """
    option_type = option_type.upper()
    assumptions = GreekAssumptions(
        risk_free_rate=risk_free_rate,
        dividend_yield=dividend_yield,
        time_basis="calendar/365" if time_basis == "calendar" else "trading/252",
        volatility_source=(
            "supplied by caller" if volatility is not None
            else "solved from market premium"
        ),
    )
    result = GreekResult(assumptions=assumptions)

    if spot is None or spot <= 0:
        result.converged = False
        result.failure_reason = "underlying price unavailable"
        return result

    T = time_to_expiry_years(expiry, now=now, basis=time_basis)
    result.time_to_expiry_years = round(T, 6)

    intrinsic = (max(0.0, spot - strike) if option_type == "CE"
                 else max(0.0, strike - spot))
    result.intrinsic_value = round(intrinsic, 4)
    result.moneyness = classify_moneyness(spot, strike, option_type)

    if T <= 0:
        result.converged = False
        result.failure_reason = "contract has expired"
        result.price = intrinsic
        result.time_value = 0.0
        return result

    sigma = volatility
    if sigma is None:
        sigma, converged, reason = implied_volatility(
            market_price or 0.0, spot, strike, T, risk_free_rate,
            dividend_yield, option_type,
        )
        result.converged = converged
        result.failure_reason = reason
        if sigma is None:
            return result
    result.implied_volatility = round(sigma, 6)

    d1, d2 = _d1_d2(spot, strike, T, risk_free_rate, dividend_yield, sigma)
    sqrt_t = math.sqrt(T)
    pdf_d1 = norm.pdf(d1)
    discount_r = math.exp(-risk_free_rate * T)
    discount_q = math.exp(-dividend_yield * T)

    theoretical = bsm_price(spot, strike, T, risk_free_rate, dividend_yield,
                            sigma, option_type)
    result.price = round(theoretical, 4)
    result.time_value = round(
        (market_price if market_price is not None else theoretical) - intrinsic, 4
    )

    if option_type == "CE":
        delta = discount_q * norm.cdf(d1)
        theta_annual = (
            -(spot * pdf_d1 * sigma * discount_q) / (2 * sqrt_t)
            - risk_free_rate * strike * discount_r * norm.cdf(d2)
            + dividend_yield * spot * discount_q * norm.cdf(d1)
        )
        rho = strike * T * discount_r * norm.cdf(d2) / 100.0
    else:
        delta = -discount_q * norm.cdf(-d1)
        theta_annual = (
            -(spot * pdf_d1 * sigma * discount_q) / (2 * sqrt_t)
            + risk_free_rate * strike * discount_r * norm.cdf(-d2)
            - dividend_yield * spot * discount_q * norm.cdf(-d1)
        )
        rho = -strike * T * discount_r * norm.cdf(-d2) / 100.0

    result.delta = round(delta, 6)
    result.gamma = round(discount_q * pdf_d1 / (spot * sigma * sqrt_t), 8)
    # Convert per-year theta to per-calendar-day, the unit traders quote.
    result.theta = round(theta_annual / CALENDAR_DAYS_PER_YEAR, 6)
    # Vega per 1 volatility *point* (i.e. a move from 20% to 21%).
    result.vega = round(spot * discount_q * pdf_d1 * sqrt_t / 100.0, 6)
    result.rho = round(rho, 6)

    result.explanations = _explain(result, spot, strike, option_type)
    return result


def classify_moneyness(spot: float, strike: float, option_type: str,
                       atm_band_pct: float = 0.5) -> str:
    """ITM / ATM / OTM. The ATM band is a stated convention, not a fact."""
    if spot <= 0:
        return "UNKNOWN"
    distance_pct = abs(strike - spot) / spot * 100.0
    if distance_pct <= atm_band_pct:
        return "ATM"
    if option_type.upper() == "CE":
        return "ITM" if strike < spot else "OTM"
    return "ITM" if strike > spot else "OTM"


def _explain(result: GreekResult, spot: float, strike: float,
             option_type: str) -> Dict[str, str]:
    """Plain-language readings that always name the model assumption."""
    out: Dict[str, str] = {}
    if result.delta is not None:
        out["delta"] = (
            f"Delta {result.delta:.3f}: under the model's assumptions the "
            f"premium changes by roughly Rs {abs(result.delta):.2f} for a Rs 1 "
            f"move in the underlying, and the sign follows the option type. "
            f"Delta is not a probability, though it is often read as one."
        )
    if result.gamma is not None:
        out["gamma"] = (
            f"Gamma {result.gamma:.5f}: delta itself changes by this much per "
            f"Rs 1 move. High gamma near expiry is why an ATM position's "
            f"exposure can flip quickly."
        )
    if result.theta is not None:
        out["theta"] = (
            f"Theta {result.theta:.3f} per calendar day: holding everything "
            f"else constant the model loses this much value each day. Weekends "
            f"decay too - the market simply prices it in around them."
        )
    if result.vega is not None:
        out["vega"] = (
            f"Vega {result.vega:.3f} per volatility point: a move in implied "
            f"volatility from, say, 20% to 21% changes the premium by about "
            f"Rs {result.vega:.2f}."
        )
    if result.rho is not None:
        out["rho"] = (
            f"Rho {result.rho:.4f} per rate point: the least significant Greek "
            f"for short-dated Indian contracts, shown for completeness."
        )
    if result.implied_volatility is not None:
        out["implied_volatility"] = (
            f"Implied volatility {result.implied_volatility * 100:.2f}% is the "
            f"annualised volatility that makes the model reproduce the traded "
            f"premium. It is an output of the model, not an observed quantity."
        )
    if not result.converged and result.failure_reason:
        out["convergence"] = (
            f"Greeks are unreliable here: {result.failure_reason}."
        )
    return out
