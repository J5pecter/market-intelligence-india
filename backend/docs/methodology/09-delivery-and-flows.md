# Delivery, open interest and disclosed flows

Source: `backend/app/services/market_flows.py`, `backend/app/services/eod_ingest.py`.

These analyses need the *exchange's* record rather than a price feed, which is
why they depend on the archive adapters.

## Delivery percentage

The share of a session's traded volume that actually settled into demat
accounts instead of being squared off intraday.

```
delivery % = deliverable quantity / traded quantity x 100
```

Two stocks can print identical candles and mean opposite things. +6% on 78%
delivery is someone taking stock off the market. +6% on 14% delivery is
intraday churn that frequently round-trips the next session. **No OHLC series
can separate those**, and no free vendor publishes the split — only the
exchange does, in the daily MTO file.

### The comparison that matters is against the stock itself

Utilities habitually deliver 70%+. Index heavyweights run 30–40%. Judging both
against one threshold just re-discovers the sector.

So the platform compares a stock's delivery against **its own** stored history
and reports a percentile:

| Percentile | Regime |
| --- | --- |
| ≥ 90 | `ACCUMULATION` |
| 70–89 | `ELEVATED` |
| 11–69 | `NORMAL` |
| ≤ 10 | `CHURN` |

Below **20 stored sessions** it reports `UNKNOWN` and says so. It does not fall
back on the market median as a baseline, because sector habits vary far too
much for that to mean anything.

That history only exists if something keeps it. The exchange publishes each
day's file and moves on; nobody backfills it for you. Hence the daily ingestion
job — and `/api/exchange/ingest/status`, which reports how many sessions are
stored and whether the percentile is usable yet.

### What it cannot tell you

Delivery is **settlement, not intent**. It cannot distinguish an institution
accumulating from a promoter pledging, and heavy delivery into a fall is
distribution just as much as heavy delivery into a rally is accumulation. The
platform states this on every delivery response rather than implying direction.

## Open-interest buildup

The standard four-way read of price change against OI change:

| Price | OI | Label | What it evidences |
| --- | --- | --- | --- |
| up | up | `LONG_BUILDUP` | new money took the long side |
| down | up | `SHORT_BUILDUP` | new money took the short side |
| up | down | `SHORT_COVERING` | shorts bought back; no new longs |
| down | down | `LONG_UNWINDING` | longs closed out |

Both a price threshold (0.1%) and an OI threshold (1.0%) must be cleared, or
the result is `INDETERMINATE`. A 0.02% price move with 0.3% more OI is
rounding, and forcing it into one of the four corners would manufacture a
signal out of noise.

**This describes what positions did, not what price will do next.** A long
buildup is routinely followed by a reversal when the crowd is offside, and a
crowded short is the fuel for a squeeze. Stock futures OI also mixes hedges,
arbitrage and directional bets, which the aggregate cannot separate.

## Disclosed deals

Bulk deals are single-client trades above 0.5% of listed equity, disclosed the
same day. Block deals execute in a separate window at a negotiated price, so
the print need not sit inside the day's regular range.

**Both legs of every deal are reported**, so gross quantity double-counts. Only
the net figure per symbol means anything, which is what the platform computes.
Where buys and sells match exactly the direction is reported as `MATCHED` — a
transfer between two reported parties rather than net accumulation.

A disclosed deal is a transaction that happened. It is not a recommendation by
the party that made it, and their reasons are not in the data.

## Market breadth

Computed from the bhavcopy: advances, declines, the A/D ratio, and the
distribution of moves.

The headline figure is the **median** scrip's change, not the mean. An index
can rise on five names while most of the market falls; the median shows what
the typical stock actually did, and the mean hides it.

## Storage and idempotency

Rows are keyed on `(symbol, exchange, session_date)` and upserted, so
re-ingesting a stored session corrects it rather than duplicating it. Deal
registers have no natural key, so a re-run replaces that day wholesale.

Every dataset writes an `IngestionRun` audit row even when nothing is stored,
because "the exchange published nothing" and "our job never ran" look identical
in the data and demand opposite responses.
