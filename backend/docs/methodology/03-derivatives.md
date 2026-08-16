# Options, Greeks and futures

Source: `backend/app/services/greeks.py` and
`backend/app/services/options_analysis.py`.

## Pricing model

Black-Scholes-Merton with a continuous dividend yield. NSE index and stock
options are European-style, which is what this model assumes.

```
d1 = (ln(S/K) + (r - q + sigma^2/2) * T) / (sigma * sqrt(T))
d2 = d1 - sigma * sqrt(T)

Call = S*e^(-qT)*N(d1) - K*e^(-rT)*N(d2)
Put  = K*e^(-rT)*N(-d2) - S*e^(-qT)*N(-d1)
```

### Assumptions, all of them configurable and all of them returned

| Assumption | Default | Note |
| --- | --- | --- |
| Risk-free rate | 6.5% | A **configured constant**, not a live curve. Every Greek scales with it. |
| Dividend yield | 0% | Override per instrument when known. |
| Time basis | calendar / 365 | The BSM convention. A trading-day variant exists for comparison and is labelled. |
| Expiry time | 15:30 IST | Indian contracts expire at the close. |
| Volatility | solved from the traded premium | An explicit value overrides the solver. |

## Greeks

| Greek | Unit reported | Meaning |
| --- | --- | --- |
| Delta | per Rs 1 of underlying | Sensitivity of the premium. **Not a probability**, though it is often read as one. |
| Gamma | per Rs 1 | How fast delta itself changes. |
| Theta | **per calendar day** | Weekends decay too; the market prices it in around them. |
| Vega | per **1 volatility point** | A move from 20% to 21%. |
| Rho | per 1 rate point | Negligible for short-dated Indian contracts; shown for completeness. |

## Implied volatility

Solved by bisection on a bracketed root over 0-500% annualised. Bisection
rather than Newton-Raphson because Newton diverges on deep out-of-the-money
contracts near expiry.

Before solving, the premium is checked against the no-arbitrage bounds:

```
Call: max(0, S*e^(-qT) - K*e^(-rT))  <=  premium  <=  S*e^(-qT)
Put:  max(0, K*e^(-rT) - S*e^(-qT))  <=  premium  <=  K*e^(-rT)
```

A premium outside those bounds cannot be produced by *any* volatility, so the
solver reports non-convergence with the reason instead of fitting a number.
Expired contracts, zero premiums and missing spot prices are all reported the
same way.

## Chain analytics

**Put/Call ratio** — `total put OI / total call OI`. A positioning fact, not a
directional forecast.

**Max pain** — the strike at which total writer payout at expiry is smallest:

```
pain(K) = sum over s<K of CE_OI(s)*(K-s) + sum over s>K of PE_OI(s)*(s-K)
```

It is an arithmetic property of *today's* open interest and moves as that open
interest changes.

**Build-up** — the standard four-quadrant reading of price change against OI
change:

| Price | OI | Reading |
| --- | --- | --- |
| up | up | Long build-up |
| down | up | Short build-up |
| up | down | Short covering |
| down | down | Long unwinding |

This describes flow, not intent. It cannot distinguish a directional bet from a
hedge, and it cannot tell you whether a large call OI is an outright position
or a covered writer.

**IV skew** — mean OTM put IV minus mean OTM call IV. Positive skew is the
usual shape in equity markets and steepens when downside protection is bid.

## Futures

```
basis            = futures - spot
basis %          = basis / spot * 100
annualised basis = basis % * 365 / days_to_expiry
```

A positive basis usually reflects cost of carry. A persistent discount more
often signals borrowing demand or dividend expectations than a directional
view.

## Limitations

Open interest shows where positions sit, not who holds them or why. Max pain
and OI concentration are worth monitoring; they are not price barriers, and
this platform does not describe them as such.
