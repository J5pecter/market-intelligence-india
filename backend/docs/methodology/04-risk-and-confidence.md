# Risk, confidence and setup arithmetic

Sources: `backend/app/services/risk.py`, `confidence.py`, `trade_status.py`.

## Setup arithmetic

The entry reference is the **worst realistic fill** for the direction traded:
the top of the range for a long, the bottom for a short. That is the
conservative choice — it never flatters the setup.

```
achieved %             = (LTP - entry_reference) / entry_reference * 100
potential from entry   = (target - entry_reference) / entry_reference * 100
potential from LTP     = (target - LTP) / LTP * 100
risk per unit          = |entry_reference - stop_loss|
reward per unit        = |final_target - entry_reference|
risk / reward          = reward / risk
R multiple (target n)  = |target_n - entry_reference| / risk
```

Equity cards emphasise **potential expected** (from the entry); option cards
emphasise **potential left** (from the current premium). Both are always
computed and both are returned — only the emphasis differs, and the label on
screen says which is which.

When the stop equals the entry, risk per unit is **zero**, so risk/reward is
reported as undefined rather than infinite.

## Status engine

Evaluated on every read, never stored as a permanent badge. Checks run in this
order, because a hit stop is terminal regardless of where price sits now:

1. **Stop reached** — using the lowest low since publication, not just the last
   price. Without that path data the evaluation says so in a warning.
2. **Target reached** — using the highest high since publication.
3. **Expired** — past the publication's validity window.
4. **Range membership** — bounds are **inclusive**, so a price exactly on
   either edge is inside the range.

When a single bar's range touches both the stop and the target, the **stop**
is taken. Tick data would be needed to resolve the order, and the optimistic
assumption is the one that flatters a setup into looking tradeable.

## Risk engine

Deliberately separate from the signal engine, so a signal never marks its own
homework. Each factor returns 0-100 where **higher is riskier**.

| Factor | Weight | Inputs |
| --- | --- | --- |
| Risk/reward | 1.5 | published levels |
| Liquidity | 1.5 | turnover, volume, spread, open interest |
| Volatility | 1.4 | ATR as % of price |
| Event risk | 1.4 | earnings and catalysts within 14 days |
| Expiry (derivatives) | 1.3 | days to expiry |
| Time decay (options) | 1.3 | theta as % of premium per day |
| Data quality | 1.2 | freshness and source reliability |
| Position concentration | 1.2 | proposed portfolio weight |
| Implied volatility | 1.1 | ATM IV level |
| Gap risk | 1.1 | recent overnight gaps |
| Sector concentration | 1.0 | sector exposure |

### The blend

```
composite = 0.7 * weighted_mean + 0.3 * worst_factor
```

Seventy per cent weighted blend plus thirty per cent worst factor, so a single
severe risk keeps its voice instead of being averaged into comfort.

Some factors are **blocking**: risk/reward below 1.0, liquidity scoring 80+,
two days or less to expiry, or data quality below 35. Any of them raises the
rating to at least HIGH regardless of the blend, and the reason is stated.

| Composite | Rating |
| --- | --- |
| >= 75 | Very high |
| >= 55 | High |
| >= 35 | Moderate |
| < 35 | Low |

When four or more dimensions could not be assessed, the payload says the rating
covers less ground than it appears to.

## Confidence

Confidence is **not a probability of profit**. It answers a narrower question:
how much does the available evidence agree, and how good is that evidence?

| Dimension | Weight |
| --- | --- |
| Technical | 1.6 |
| Fundamental | 1.4 |
| Options | 1.2 |
| Historical | 1.1 |
| Volume | 1.0 |
| News | 0.9 |
| Catalyst | 0.8 |

```
base     = sum(score * weight) / sum(weight)      # scored dimensions only
coverage = scored_weight / expected_weight
overall  = base - conflict_penalty - coverage_penalty - data_quality_penalty
```

**Conflict penalty** — `10 + 12 * balance` where balance is how evenly the
disagreement splits. Two dimensions each way is penalised more than four
against one.

**Coverage penalty** — up to about 20 points as coverage falls below 85%. A
signal built on one lonely indicator must not score like one corroborated by
six.

**Data-quality penalty** — `(70 - quality) * 0.35` when quality is below 70.

### The state it produces

| State | Condition |
| --- | --- |
| `INSUFFICIENT_EVIDENCE` | coverage below 35% |
| `MIXED_WAIT_FOR_CONFIRMATION` | any conflict detected |
| `EVIDENCE_ALIGNED` | overall >= 70 |
| `EVIDENCE_LEANING` | overall >= 50 |
| `EVIDENCE_WEAK` | otherwise |

The setup generator refuses to produce a direction in the first two states.

## Position sizing and charges

```
max rupee risk = capital * max_loss_pct / 100
quantity       = floor(max_rupee_risk / |entry - stop|)   # rounded to lot size
```

The stop is assumed to fill at its stated price. Gaps and slippage are not
modelled there, and can make the realised loss larger than the budget — the
payload says so.

Charges are modelled from published statutory rates plus a configurable
brokerage assumption: STT, exchange transaction charges, SEBI turnover fee,
stamp duty and GST, itemised on every scenario. They are indicative only; your
contract note is the authority. The base scenario — exit at entry — is
therefore always **negative**, which is the point of showing it.
