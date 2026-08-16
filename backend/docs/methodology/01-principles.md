# Principles

This platform is a research terminal, not a signal service. Everything below
follows from four rules that are enforced in code, not just stated here.

## 1. Nothing appears without provenance

Every value that reaches a screen travels inside a `Sourced` envelope carrying
the provider, the source name, the observation timestamp and a **data status**:

| Status | Meaning |
| --- | --- |
| `LIVE` | Observed inside this capability's freshness window. |
| `DELAYED` | The provider is known to be delayed. Not an exchange feed. |
| `STALE` | Older than the acceptable window. The last successful update, not live data. |
| `UNAVAILABLE` | No provider returned a value. |
| `ESTIMATED` | Derived or modelled, not observed. |
| `MANUAL` | Entered by an operator through the admin panel. |
| `UNVERIFIED` | A third-party aggregate that has not been cross-checked. |
| `DEMO` | Seeded sample data shipped with the repository. Never market data. |

A stale value is never redrawn as a live one. When every provider fails, the
panel says so and shows the reason rather than the last number it happened to
have.

## 2. A missing input is reported, never treated as neutral

If RSI cannot be computed, the technical chain records a **data gap** and the
score is computed on what remains, with coverage reported separately. A metric
that could not be measured is excluded from both the numerator and the
denominator — it never counts as a quiet pass.

## 3. Conflict survives to the output

When dimensions disagree — fundamentals constructive, technicals weak — the
confidence engine reports `MIXED_WAIT_FOR_CONFIRMATION` and the setup generator
refuses to produce a direction. Mixed is a legitimate answer, and it is a more
useful one than a fabricated verdict.

## 4. No number without its arithmetic

Every evidence item carries `metric -> value -> calculation -> interpretation
-> source -> timestamp`. The "Why?" panel is rendered mechanically from those
fields, so the interface cannot show a conclusion the data does not contain.

## What this platform does not do

- It does not predict prices.
- It does not attach probabilities to outcomes unless a stated method produces
  them, and it labels the method when it does.
- It does not present third-party research as its own.
- It does not claim any registration it has not been configured with.
- It does not guarantee, imply or model a profit.
